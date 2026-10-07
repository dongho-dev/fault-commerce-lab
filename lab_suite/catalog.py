from importlib import import_module

BASELINE = "a3b8ef9e20366d85b5d74e13ef3bd988f98b6353"
CASES = {
    "01": ("data", "Global price ordering"),
    "02": ("data", "Order atomicity"),
    "03": ("data", "Postal code representation"),
    "04": ("systems", "Cumulative resource exhaustion"),
    "05": ("systems", "Incomplete response transmission"),
    "06": ("systems", "Multi-instance inventory visibility"),
    "07": ("browser", "Out-of-order search responses"),
    "08": ("browser", "Product description trust boundary"),
    "09": ("browser", "Deployment asset completeness"),
    "10": ("browser", "Accessible product controls"),
    "11": ("advanced_data", "Order completion"),
    "12": ("advanced_data", "Limited sale order quantities"),
    "13": ("advanced_systems", "Storefront availability"),
    "14": ("advanced_systems", "Intermittent product browsing"),
    "15": ("advanced_cache", "New product visibility"),
    "16": ("advanced_round2_a", "Customer report 16"),
    "17": ("advanced_round2_a", "Customer report 17"),
    "18": ("advanced_round2_b", "Customer report 18"),
    "19": ("advanced_round2_b", "Customer report 19"),
    "20": ("advanced_round2_c", "Customer report 20"),
    "21": ("advanced_round2_c", "Customer report 21"),
}


def normalize(value: str) -> str:
    case = str(int(value.removeprefix("case").removeprefix("cs-"))).zfill(2)
    if case not in CASES:
        raise ValueError(f"Unknown case: {value}")
    return case


def provider(case: str):
    return import_module(f"lab_suite.cases.{CASES[normalize(case)][0]}")
