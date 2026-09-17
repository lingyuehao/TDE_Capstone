"""Charge category taxonomy, shared by charge_categorization.ipynb and
charge_categorization_check.ipynb so the two notebooks can never drift apart.

Patterns are lowercase regex fragments matched against `Charge Type` and
`Charge Description` combined into one string (see normalize/rule_classify_row) --
not tried separately field-by-field. An earlier version tried `Charge Type` to
completion first and fell back to `Charge Description` only if nothing matched;
that badly undercounted subcategories like "Ground", because a generic
`Charge Type` value like "Freight" would match a catch-all pattern before ever
looking at `Charge Description`, where the real detail (e.g. "Ground Commercial")
actually lives.

Within each major, order matters: more specific subcategories are listed before
generic ones (e.g. "Chargeback Fuel Surcharge" before the generic fuel pattern;
"Ground" before the generic "Transportation / Base Charge" fallback).
"""

import re

import pandas as pd

TAXONOMY = {
    "Fuel Surcharge": {
        "Fuel Surcharge Correction": [r"correction.*fuel", r"fuel.*correction"],
        "Chargeback Fuel Surcharge": [r"chargeback.*fuel"],
        "International Fuel Surcharge": [r"worldease.*fuel", r"international.*fuel"],
        "Domestic Fuel Surcharge": [r"\bfuel surcharge\b", r"\bfuel\b", r"brandstof"],
    },
    "Accessorial / Delivery Surcharge": {
        "Delivery Area Surcharge (DAS)": [r"\bdas\b", r"delivery area surcharge"],
        "Residential Delivery/Surcharge": [r"residential"],
        "Signature Required": [r"signature"],
        "Additional Handling": [r"add'?l handling", r"additional handling", r"handling.*dimension", r"liftgate", r"non.?conveyable"],
        "Saturday / After Hours / Holiday": [r"saturday", r"after hours", r"\bholiday\b"],
        "Demand / Peak Surcharge": [r"demand surcharge", r"surge emergency", r"\bpeak\b"],
        "Wait Time / Mileage / Toll": [r"wait time", r"additional miles", r"\btoll\b"],
        "Address Correction": [r"address correction"],
        "Security Surcharge": [r"security surcharge", r"\bscreening\b", r"x-ray"],
        # Symmetric counterpart to "Residential Delivery/Surcharge". Deliberately narrow
        # (only "commercial", not "business") -- raw data has garbage commodity-description
        # rows like "Business or Office Machines, viz." that a bare `\bbusiness\b` pattern
        # would wrongly sweep in. Freight service-tier variants ("Ground Commercial",
        # "Next Day Air Commercial") are pulled out to Line Haul / Base Transportation before this ever runs --
        # see PRIORITY_OVERRIDES.
        "Commercial / Business Delivery Surcharge": [r"\bcommercial\b"],
    },
    "Discounts": {
        "Earned Discount": [r"earned discount"],
        "Grace Discount": [r"grace discount"],
        "General Discount": [r"\bdiscount\b"],
    },
    "Credits": {
        "Credit From Carrier": [r"credit from carrier"],
    },
    "Taxes & Customs": {
        "VAT": [r"\bvat\b", r"\bbtw\b"],
        "GST / HST": [r"\bgst\b", r"\bhst\b"],
        "Duty & Import Tax": [r"dut(y|ies)", r"import fee", r"import tax"],
        "Customs / Brokerage": [r"customs?", r"brokerage", r"export declaration", r"international processing", r"internationale verwerkingskosten", r"\beei\b", r"commercial invoice", r"entry line charge"],
        "Sales / General Tax": [r"\btax(es)?\b"],
    },
    "Administrative & Service Fees": {
        # Always caught earlier by PRIORITY_OVERRIDES; kept here too so it still shows up
        # as a documented subcategory and as a TF-IDF fallback reference candidate.
        "Chargeback / Reversal": [r"\bchargeback\b"],
        # Row-level check (2026-09-16): 99.1% of the rows matching these patterns are
        # POSITIVE charges (e.g. "Shipping Charge Correction Ground" is 4,376 positive vs.
        # 6 negative rows) -- this is billing/accounting corrections that add to the
        # invoice, not a price concession or a carrier refund, so it belongs with the other
        # administrative billing fees, not under Discounts or Credits.
        "Billing Adjustment / Correction": [r"billing adjustment", r"shipping charge correction", r"verzendcorrectiekosten", r"\brebill\b"],
        "Disbursement Fee": [r"disbursement"],
        "Third Party Billing": [r"third party billing"],
        "Document Fee": [r"document fee", r"documents? preparation"],
        "Returns / Print Label Fee": [r"print.*label", r"returns print label"],
        "Declared Value / Insurance": [r"declared value"],
        "Sustainability / Carbon Fee": [r"gogreen", r"carbon reduced"],
        "Package Handling / Storage": [r"package handling", r"warehouse storage", r"\bstorage\b"],
        "Pickup Service": [r"pickup"],
    },
    # Major is named "Line Haul / Base Transportation" (not e.g. "Base Freight /
    # Transportation") because that's the umbrella concept: the core movement charge,
    # as opposed to Fuel Surcharge / Accessorial / Taxes / Administrative. Ground, Air,
    # International, and Ocean Freight are *mode* subcategories -- the raw label told us
    # which mode. "General / Mode Not Specified" is NOT a fifth mode; it's the fallback for
    # when the raw label just says "Freight" / "Base" / "Transportation Charge" / "Line
    # Haul" without naming a mode at all -- in the sample, 87.7% of that bucket is the
    # literal label "Transportation Charge" and only 1.8% literally says "line haul".
    "Line Haul / Base Transportation": {
        "Ground": [r"\bground\b", r"\bltl\b", r"truckload", r"road ?freight"],
        # Broadened beyond just the named express tiers to also catch generic "air
        # freight"/"airfreight" labels and airport-transfer legs (incl. "AFS AIR FREIGHT
        # SURCHARGE ...", "Airline Airfreight Surcharge", "AIRPORT TRANSFER" -- these
        # literally name air-mode terms, so pulling them out of the generic bucket is
        # evidence-based, not a guess at what they mean beyond that). "rport transfer"
        # (not "airport transfer") also catches an OCR-mangled variant seen in the raw
        # data: "Al RPORT TRANSFER E".
        "Air Freight": [r"next day", r"2nd day", r"two day", r"3 day", r"third day", r"second day", r"\bair ?freight\b", r"rport transfer"],
        "International / Export / Import Freight": [r"worldwide express", r"ww express", r"\bexport\b", r"\bimport\b", r"world ?ease", r"international freight"],
        "Ocean Freight": [r"ocean"],
        "General / Mode Not Specified": [r"line haul", r"transportation charge", r"\bbase\b", r"\bfreight\b", r"frt freight"],
    },
}

CATCH_ALL = ("Other / Uncategorized", "Unclassified")

# Checked BEFORE the main taxonomy loop -- these are cases where a generic pattern
# elsewhere in TAXONOMY would otherwise fire first and give the wrong answer.
PRIORITY_OVERRIDES = [
    # Fuel-specific chargebacks must win over the generic chargeback override below.
    (r"chargeback.*fuel", ("Fuel Surcharge", "Chargeback Fuel Surcharge")),
    # A chargeback/reversal is classified by what kind of billing event it is, not by
    # what the underlying charge was for -- so this must outrank Line Haul / Base
    # Transportation's mode patterns and Accessorial's "commercial"/"residential"
    # patterns for things like "Chargeback Ground Commercial".
    (r"\bchargeback\b", ("Administrative & Service Fees", "Chargeback / Reversal")),
    # "Commercial Invoice" is a customs document, not a delivery surcharge -- must be
    # pulled out before Accessorial's "Commercial / Business Delivery Surcharge" pattern.
    (r"commercial invoice", ("Taxes & Customs", "Customs / Brokerage")),
    # "Ground Residential" / "Next Day Air Commercial" etc. are freight *service-tier*
    # names (the residential- or commercial-rate version of that service), not a
    # standalone residential/commercial delivery surcharge line -- without this override
    # they'd be caught by Accessorial's generic "residential"/"commercial" patterns
    # before Line Haul / Base Transportation is ever reached.
    (r"\bground\b.*\bresidential\b", ("Line Haul / Base Transportation", "Ground")),
    (r"\bground\b.*\bcommercial\b", ("Line Haul / Base Transportation", "Ground")),
    (r"\b(next day|2nd day|second day|3 day|third day)\b.*\bresidential\b", ("Line Haul / Base Transportation", "Air Freight")),
    (r"\b(next day|2nd day|second day|3 day|third day)\b.*\bcommercial\b", ("Line Haul / Base Transportation", "Air Freight")),
    # "Transport to port" -> Ocean Freight. This is an override rather than a TAXONOMY
    # pattern on purpose: TF-IDF reference docs are built FROM the TAXONOMY patterns (see
    # charge_categorization.ipynb), so putting a phrase containing the generic word
    # "transport" there would leak "transport" into Ocean Freight's fallback reference
    # doc -- which then fuzzy-matches unrelated "Road transport" / "Dedicated transport"
    # labels into Ocean Freight too (found this by checking the re-run's output, not
    # hypothetically -- it actually happened). Overrides never feed the TF-IDF corpus, so
    # this stays a precise, narrow rule without that side effect.
    (r"transport to port", ("Line Haul / Base Transportation", "Ocean Freight")),
]

# Subcategories excluded from the TF-IDF fallback reference corpus (see ref_docs
# construction in charge_categorization.ipynb / _check.ipynb). Checked empirically, not
# hypothetically: every literal "chargeback" mention in the data is already caught
# exactly by the `\bchargeback\b` override/rule above (there are zero "charge back" /
# "charge-back" spelling variants in the raw data that would need fuzzy matching to
# catch), so this subcategory's reference doc -- just the single word "chargeback" --
# had no true positives to contribute via fallback. What it DID contribute: because
# "chargeback" shares its first 6 characters with the extremely common substring
# "charge" (present in hundreds of unrelated raw labels -- "Service Charge", "Weekend
# Charge", "Order Lane Charge", "Terminal Charges", ...), character n-gram TF-IDF scored
# many of them similar enough to fuzzy-match into "Chargeback / Reversal" -- a 100%
# false-positive rate across the 706 rows it had pulled in this way. Excluding it from
# the candidate pool removes that failure mode entirely without weakening real chargeback
# detection at all, since the exact-match path was already exhaustive.
TFIDF_EXCLUDE = {
    ("Administrative & Service Fees", "Chargeback / Reversal"),
    # Same failure mode, same fix: this reference doc is "chargeback fuel", which still
    # shares "charge" with the same flood of generic "___ Charge" labels -- confirmed
    # they leaked in here too once the other "chargeback" doc was excluded (e.g. "Order
    # Lane Charge", "Weekend Charge" fuzzy-matched here instead). `chargeback.*fuel` in
    # PRIORITY_OVERRIDES is exhaustive for real fuel chargebacks, so nothing real is lost.
    ("Fuel Surcharge", "Chargeback Fuel Surcharge"),
}


def normalize(text):
    if pd.isna(text):
        return ""
    return str(text).lower().strip()


def rule_classify_row(charge_type, charge_desc):
    t = (normalize(charge_type) + " " + normalize(charge_desc)).strip()
    if not t:
        return None
    for pat, result in PRIORITY_OVERRIDES:
        if re.search(pat, t):
            return result
    for major, subcats in TAXONOMY.items():
        for sub, patterns in subcats.items():
            for pat in patterns:
                if re.search(pat, t):
                    return (major, sub)
    return None
