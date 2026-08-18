import csv
import io
import json
import urllib.request
from datetime import datetime, timezone


# =========================================================
# BIS GLOBAL LIQUIDITY DATA
# =========================================================

API_URL = (
    "https://stats.bis.org/api/v2/data/dataflow/"
    "BIS/WS_GLI/1.0/"
    "?format=sdmx-csv-1.0.0&labels=both"
)

BULK_URL = (
    "https://data.bis.org/static/bulk/"
    "WS_GLI_csv_flat.zip"
)


# =========================================================
# DOWNLOAD
# =========================================================

def download(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "BondStats Global Liquidity Monitor/1.0",
            "Accept":
                "text/csv,application/octet-stream,*/*"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=90
    ) as response:

        return response.read()


# =========================================================
# BASIC HELPERS
# =========================================================

def clean(value):

    if value is None:
        return ""

    return (
        str(value)
        .replace("\xa0", " ")
        .strip()
    )


def number(value):

    value = clean(value)

    if not value:
        return None

    value = value.replace(",", "")

    try:
        return float(value)

    except ValueError:
        return None


def find_column(
    fieldnames,
    wanted
):

    wanted = wanted.upper()

    for field in fieldnames:

        if clean(field).upper() == wanted:
            return field

    # Some CSV representations may prefix
    # or slightly modify the SDMX heading.
    for field in fieldnames:

        if wanted in clean(field).upper():
            return field

    return None


def is_code(value, code):
    """
    Supports both raw SDMX codes:
        USD

    and labelled representations such as:
        USD: US dollar
        USD - US dollar
        USD, US dollar
    """

    value = clean(value)
    code = clean(code)

    if value == code:
        return True

    upper_value = value.upper()
    upper_code = code.upper()

    return (
        upper_value.startswith(
            upper_code + ":"
        )
        or upper_value.startswith(
            upper_code + " -"
        )
        or upper_value.startswith(
            upper_code + ","
        )
        or upper_value.startswith(
            upper_code + " "
        )
    )


# =========================================================
# LOAD BIS DATA
# =========================================================

def load_api_csv():

    print(
        "Downloading BIS Global Liquidity data..."
    )

    raw = download(API_URL)

    text = raw.decode(
        "utf-8-sig",
        errors="replace"
    )

    if "TIME_PERIOD" not in text:
        raise RuntimeError(
            "BIS API response does not contain "
            "TIME_PERIOD."
        )

    if "OBS_VALUE" not in text:
        raise RuntimeError(
            "BIS API response does not contain "
            "OBS_VALUE."
        )

    print("BIS API successful.")

    return text


# =========================================================
# PARSE SDMX CSV
# =========================================================

def parse_rows(text):

    reader = csv.DictReader(
        io.StringIO(text)
    )

    rows = list(reader)

    if not rows:
        raise RuntimeError(
            "BIS API returned no observations."
        )

    fields = reader.fieldnames or []

    required = {
        "freq":
            find_column(
                fields,
                "FREQ"
            ),

        "currency":
            find_column(
                fields,
                "CURR_DENOM"
            ),

        "borrower_country":
            find_column(
                fields,
                "BORROWERS_CTY"
            ),

        "borrower_sector":
            find_column(
                fields,
                "BORROWERS_SECTOR"
            ),

        "lender_sector":
            find_column(
                fields,
                "LENDERS_SECTOR"
            ),

        "position":
            find_column(
                fields,
                "L_POS_TYPE"
            ),

        "instrument":
            find_column(
                fields,
                "L_INSTR"
            ),

        "unit":
            find_column(
                fields,
                "UNIT_MEASURE"
            ),

        "time":
            find_column(
                fields,
                "TIME_PERIOD"
            ),

        "value":
            find_column(
                fields,
                "OBS_VALUE"
            )
    }

    missing = [
        key
        for key, value
        in required.items()
        if value is None
    ]

    if missing:

        print(
            "Columns received from BIS:"
        )

        for field in fields:
            print(" -", field)

        raise RuntimeError(
            "Missing BIS SDMX columns: "
            + ", ".join(missing)
        )

    return rows, required


# =========================================================
# EXACT BIS SERIES SELECTION
# =========================================================

def matching_rows(
    rows,
    columns,
    currency,
    unit
):

    result = []

    for row in rows:

        # Exact BIS GLI series structure:
        #
        # Q
        # currency
        # 3P = borrowers outside currency area
        # N  = non-banks, total
        # A  = all lending sectors
        # I  = cross-border & local in FCY
        # B  = credit (loans & debt securities)
        # unit = currency or 771 (YoY growth)

        if not is_code(
            row.get(
                columns["freq"]
            ),
            "Q"
        ):
            continue

        if not is_code(
            row.get(
                columns["currency"]
            ),
            currency
        ):
            continue

        if not is_code(
            row.get(
                columns[
                    "borrower_country"
                ]
            ),
            "3P"
        ):
            continue

        if not is_code(
            row.get(
                columns[
                    "borrower_sector"
                ]
            ),
            "N"
        ):
            continue

        if not is_code(
            row.get(
                columns[
                    "lender_sector"
                ]
            ),
            "A"
        ):
            continue

        if not is_code(
            row.get(
                columns["position"]
            ),
            "I"
        ):
            continue

        if not is_code(
            row.get(
                columns["instrument"]
            ),
            "B"
        ):
            continue

        if not is_code(
            row.get(
                columns["unit"]
            ),
            unit
        ):
            continue

        period = clean(
            row.get(
                columns["time"]
            )
        )

        value = number(
            row.get(
                columns["value"]
            )
        )

        if not period:
            continue

        if value is None:
            continue

        result.append(
            {
                "period":
                    period,

                "value":
                    value
            }
        )

    result.sort(
        key=lambda x:
            x["period"]
    )

    return result


# =========================================================
# GET ONE CURRENCY
# =========================================================

def extract_currency(
    rows,
    columns,
    currency
):

    amount_rows = matching_rows(
        rows,
        columns,
        currency,
        currency
    )

    growth_rows = matching_rows(
        rows,
        columns,
        currency,
        "771"
    )

    if not amount_rows:

        raise RuntimeError(
            f"No outstanding credit series "
            f"found for {currency}. "
            f"Expected BIS series structure "
            f"Q.{currency}.3P.N.A.I.B.{currency}"
        )

    latest_amount = amount_rows[-1]

    # Prefer growth observation from same period.
    growth_by_period = {
        item["period"]:
            item["value"]
        for item in growth_rows
    }

    growth = growth_by_period.get(
        latest_amount["period"]
    )

    growth_period = (
        latest_amount["period"]
        if growth is not None
        else None
    )

    # If same-quarter growth is unavailable,
    # use latest valid growth observation.
    if (
        growth is None
        and growth_rows
    ):

        latest_growth = (
            growth_rows[-1]
        )

        growth = (
            latest_growth["value"]
        )

        growth_period = (
            latest_growth["period"]
        )

    return {
        "amount":
            latest_amount["value"],

        "period":
            latest_amount["period"],

        "growth":
            growth,

        "growthPeriod":
            growth_period
    }


# =========================================================
# REGIME
# =========================================================

def classify_regime(
    usd,
    eur,
    jpy
):

    growth_values = [
        item["growth"]
        for item in (
            usd,
            eur,
            jpy
        )
        if item["growth"]
        is not None
    ]

    if len(growth_values) < 2:

        return {
            "name":
                "MIXED SIGNAL",

            "description":
                "Available global liquidity "
                "indicators do not yet provide "
                "a sufficiently broad directional "
                "signal."
        }

    positive = sum(
        value > 0
        for value
        in growth_values
    )

    negative = sum(
        value < 0
        for value
        in growth_values
    )

    strong_positive = sum(
        value >= 5
        for value
        in growth_values
    )

    if strong_positive >= 2:

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

    if positive == len(
        growth_values
    ):

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


# =========================================================
# MAIN
# =========================================================

def main():

    text = load_api_csv()

    rows, columns = parse_rows(
        text
    )

    print(
        f"Received {len(rows):,} "
        "BIS observations."
    )


    # -----------------------------------------------------
    # USD
    # -----------------------------------------------------

    print(
        "Reading US dollar liquidity..."
    )

    usd = extract_currency(
        rows,
        columns,
        "USD"
    )

    print(
        "USD:",
        usd
    )


    # -----------------------------------------------------
    # EUR
    # -----------------------------------------------------

    print(
        "Reading euro liquidity..."
    )

    eur = extract_currency(
        rows,
        columns,
        "EUR"
    )

    print(
        "EUR:",
        eur
    )


    # -----------------------------------------------------
    # JPY
    # -----------------------------------------------------

    print(
        "Reading yen liquidity..."
    )

    jpy = extract_currency(
        rows,
        columns,
        "JPY"
    )

    print(
        "JPY:",
        jpy
    )


    # -----------------------------------------------------
    # REGIME
    # -----------------------------------------------------

    regime = classify_regime(
        usd,
        eur,
        jpy
    )


    # Conservative common data period:
    # oldest latest observation among
    # the three currencies.

    common_period = min(
        usd["period"],
        eur["period"],
        jpy["period"]
    )


    output = {

        "ok":
            True,

        "title":
            "Global Liquidity Monitor",

        "source":
            "Bank for International Settlements",

        "dataset":
            "Global Liquidity Indicators",

        "period":
            common_period,

        "updatedAt":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "regime":
            regime,

        "currencies": {

            "usd": {

                "label":
                    "US Dollar Credit",

                "scope":
                    "Non-bank borrowers outside "
                    "the United States",

                "currency":
                    "USD",

                **usd
            },


            "eur": {

                "label":
                    "Euro Credit",

                "scope":
                    "Non-bank borrowers outside "
                    "the euro area",

                "currency":
                    "EUR",

                **eur
            },


            "jpy": {

                "label":
                    "Yen Credit",

                "scope":
                    "Non-bank borrowers outside "
                    "Japan",

                "currency":
                    "JPY",

                **jpy
            }
        }
    }


    # -----------------------------------------------------
    # VALIDATION
    # -----------------------------------------------------

    for key in (
        "usd",
        "eur",
        "jpy"
    ):

        item = (
            output["currencies"][key]
        )

        if (
            item["amount"]
            is None
            or item["amount"] <= 0
        ):

            raise RuntimeError(
                f"Invalid {key.upper()} "
                "liquidity value."
            )


    # -----------------------------------------------------
    # WRITE ONLY AFTER EVERYTHING PASSED
    # -----------------------------------------------------

    with open(
        "data.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )


    print(
        "Global liquidity data "
        "successfully written "
        "to data.json."
    )


if __name__ == "__main__":
    main()
