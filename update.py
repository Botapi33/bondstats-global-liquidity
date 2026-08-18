import csv
import io
import json
import os
import re
import urllib.request
import zipfile
from datetime import datetime, timezone


API_URL = (
    "https://stats.bis.org/api/v2/data/dataflow/"
    "BIS/WS_GLI/1.0/"
    "?format=sdmx-csv-1.0.0&labels=both"
)

BULK_URL = (
    "https://data.bis.org/static/bulk/"
    "WS_GLI_csv_flat.zip"
)


def download(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BondStats/1.0"
        }
    )

    with urllib.request.urlopen(
        req,
        timeout=60
    ) as response:
        return response.read()


def get_source_csv():

    # First try the official BIS SDMX API
    try:
        print("Trying BIS SDMX API...")

        raw = download(API_URL)

        text = raw.decode(
            "utf-8-sig",
            errors="replace"
        )

        if "TIME_PERIOD" in text and "OBS_VALUE" in text:
            print("BIS API successful.")
            return text, "BIS SDMX API"

    except Exception as e:
        print(
            "API request failed:",
            str(e)
        )

    # Official BIS bulk fallback
    print("Trying BIS bulk fallback...")

    raw_zip = download(BULK_URL)

    with zipfile.ZipFile(
        io.BytesIO(raw_zip)
    ) as z:

        csv_files = [
            name
            for name in z.namelist()
            if name.lower().endswith(".csv")
        ]

        if not csv_files:
            raise RuntimeError(
                "No CSV file found in BIS archive."
            )

        with z.open(csv_files[0]) as f:
            text = f.read().decode(
                "utf-8-sig",
                errors="replace"
            )

    print("BIS bulk fallback successful.")

    return text, "BIS Global Liquidity Bulk Data"


def clean(value):
    return (
        str(value or "")
        .replace("\xa0", " ")
        .strip()
    )


def numeric(value):

    value = clean(value)

    value = value.replace(",", "")

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        value
    )

    if not match:
        return None

    try:
        return float(match.group())
    except ValueError:
        return None


def normalized_row_text(row):

    return " | ".join(
        clean(v).lower()
        for v in row.values()
        if v is not None
    )


def get_column(fieldnames, target):

    target = target.lower()

    for field in fieldnames:

        if target in field.lower():
            return field

    return None


def rows_from_csv(text):

    reader = csv.DictReader(
        io.StringIO(text)
    )

    rows = list(reader)

    if not rows:
        raise RuntimeError(
            "BIS dataset returned no rows."
        )

    return rows, reader.fieldnames


def contains_any(text, values):

    return any(
        value.lower() in text
        for value in values
    )


def currency_candidates(
    rows,
    time_col,
    value_col,
    currency_terms,
    outside_terms
):

    result = []

    for row in rows:

        text = normalized_row_text(row)

        if not contains_any(
            text,
            currency_terms
        ):
            continue

        if not contains_any(
            text,
            outside_terms
        ):
            continue

        value = numeric(
            row.get(value_col)
        )

        period = clean(
            row.get(time_col)
        )

        if (
            value is None
            or not period
        ):
            continue

        result.append(
            {
                "text": text,
                "period": period,
                "value": value
            }
        )

    return result


def latest_matching(
    candidates,
    include_terms,
    exclude_terms=None
):

    exclude_terms = (
        exclude_terms or []
    )

    filtered = []

    for row in candidates:

        text = row["text"]

        if include_terms:

            if not contains_any(
                text,
                include_terms
            ):
                continue

        if exclude_terms:

            if contains_any(
                text,
                exclude_terms
            ):
                continue

        filtered.append(row)

    if not filtered:
        return None

    filtered.sort(
        key=lambda x: x["period"]
    )

    return filtered[-1]


def extract_currency(
    rows,
    time_col,
    value_col,
    currency_terms,
    outside_terms,
    currency_name
):

    candidates = currency_candidates(
        rows,
        time_col,
        value_col,
        currency_terms,
        outside_terms
    )

    # Outstanding stock
    amount = latest_matching(
        candidates,
        [
            "amount outstanding",
            "outstanding"
        ],
        [
            "annual change",
            "year-on-year",
            "growth"
        ]
    )

    # YoY growth
    growth = latest_matching(
        candidates,
        [
            "annual change",
            "year-on-year",
            "yoy",
            "growth"
        ]
    )

    # Fallback when labels differ slightly
    if amount is None:

        amount = latest_matching(
            candidates,
            [],
            [
                "annual change",
                "year-on-year"
            ]
        )

    if amount is None:

        raise RuntimeError(
            f"Could not identify {currency_name} "
            "credit outstanding series."
        )

    return {
        "amount": amount["value"],
        "period": amount["period"],
        "growth": (
            growth["value"]
            if growth
            else None
        ),
        "growthPeriod": (
            growth["period"]
            if growth
            else None
        )
    }


def classify_growth(
    usd,
    eur,
    jpy
):

    values = [
        item["growth"]
        for item in [
            usd,
            eur,
            jpy
        ]
        if item["growth"]
        is not None
    ]

    if len(values) < 2:

        return {
            "name": "MIXED SIGNAL",
            "description":
                "Available global liquidity "
                "indicators do not yet provide "
                "a sufficiently broad directional "
                "signal."
        }

    positive = sum(
        1
        for x in values
        if x > 0
    )

    negative = sum(
        1
        for x in values
        if x < 0
    )

    strong = sum(
        1
        for x in values
        if x >= 5
    )

    if strong >= 2:

        return {
            "name":
                "BROAD LIQUIDITY EXPANSION",

            "description":
                "Foreign-currency credit is "
                "expanding across multiple major "
                "funding currencies, indicating "
                "broad growth in international "
                "credit availability."
        }

    if positive == len(values):

        return {
            "name":
                "LIQUIDITY EXPANSION",

            "description":
                "Foreign-currency credit is "
                "growing across the major funding "
                "currencies tracked by the BIS."
        }

    if negative >= 2:

        return {
            "name":
                "LIQUIDITY CONTRACTION",

            "description":
                "Foreign-currency credit is "
                "contracting across multiple major "
                "funding currencies, pointing to "
                "tighter international financing "
                "conditions."
        }

    return {
        "name":
            "DIVERGENT LIQUIDITY",

        "description":
            "Global funding conditions are moving "
            "in different directions across major "
            "currencies, producing a mixed "
            "international liquidity signal."
    }


def main():

    csv_text, source = get_source_csv()

    rows, fieldnames = rows_from_csv(
        csv_text
    )

    time_col = get_column(
        fieldnames,
        "TIME_PERIOD"
    )

    value_col = get_column(
        fieldnames,
        "OBS_VALUE"
    )

    if not time_col:
        raise RuntimeError(
            "TIME_PERIOD column not found."
        )

    if not value_col:
        raise RuntimeError(
            "OBS_VALUE column not found."
        )

    usd = extract_currency(
        rows,
        time_col,
        value_col,
        [
            "us dollar",
            "usd"
        ],
        [
            "outside the united states",
            "outside united states",
            "non-us"
        ],
        "US dollar"
    )

    eur = extract_currency(
        rows,
        time_col,
        value_col,
        [
            "euro",
            "eur"
        ],
        [
            "outside the euro area",
            "outside euro area",
            "non-euro area"
        ],
        "euro"
    )

    jpy = extract_currency(
        rows,
        time_col,
        value_col,
        [
            "japanese yen",
            "jp yen",
            "jpy"
        ],
        [
            "outside japan",
            "non-japan"
        ],
        "yen"
    )

    regime = classify_growth(
        usd,
        eur,
        jpy
    )

    periods = [
        usd["period"],
        eur["period"],
        jpy["period"]
    ]

    latest_period = sorted(
        periods
    )[-1]

    output = {

        "ok": True,

        "title":
            "Global Liquidity Monitor",

        "source": source,

        "updatedAt":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "period":
            latest_period,

        "regime":
            regime,

        "currencies": {

            "usd": {
                "label":
                    "US Dollar Credit",

                "scope":
                    "Borrowers outside the United States",

                **usd
            },

            "eur": {
                "label":
                    "Euro Credit",

                "scope":
                    "Borrowers outside the euro area",

                **eur
            },

            "jpy": {
                "label":
                    "Yen Credit",

                "scope":
                    "Borrowers outside Japan",

                **jpy
            }
        }
    }

    # Never replace valid data with broken output
    if not output["currencies"]["usd"]["amount"]:
        raise RuntimeError(
            "USD data validation failed."
        )

    with open(
        "data.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )

    print(
        "Global liquidity data updated."
    )


if __name__ == "__main__":
    main()
