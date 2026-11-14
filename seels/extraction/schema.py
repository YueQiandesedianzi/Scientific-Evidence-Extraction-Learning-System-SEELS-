"""Extraction target schema + shared helpers.

Target schema (what SEELS tries to extract per paper):
  Sample identity      : name/label, chemical formula, phase/structure
  Composition          : list of {component, amount(value+unit/basis)}
  Experimental cond.   : temperature, pressure, atmosphere, time, ...
  Test method          : technique, instrument, standard
  Performance results  : quantity, value_raw, unit_raw (+ standardized)

Field-level provenance container (FieldVal):
  {"value": any|None, "unit": str|None, "raw": str|None,
   "evidence_ids": [str], "null_reason": str|None}
A field is EITHER grounded (value + >=1 evidence) OR null with a reason.
"""
from __future__ import annotations

from ..util import new_id, utc_now


def field_val(value=None, unit=None, raw=None, evidence_ids=None, null_reason=None) -> dict:
    return {"value": value, "unit": unit, "raw": raw,
            "evidence_ids": list(evidence_ids or []), "null_reason": null_reason}


# quantity aliases -> canonical quantity names (used by rule + QC modules)
QUANTITY_ALIASES = {
    "tc": "critical_temperature", "t_c": "critical_temperature",
    "critical temperature": "critical_temperature",
    "tg": "glass_transition_temperature", "tm": "melting_temperature",
    "tn": "neel_temperature",
    "sigma": "symbol_sigma_ambiguous", "\u03c3": "symbol_sigma_ambiguous",
    "conductivity": "electrical_conductivity",
    "rho": "symbol_rho_ambiguous", "\u03c1": "symbol_rho_ambiguous",
    "resistivity": "resistivity", "density": "density",
    "zt": "thermoelectric_zt", "seebeck": "seebeck_coefficient",
    "s": "seebeck_coefficient_ambiguous",
    "eg": "band_gap", "band gap": "band_gap", "bandgap": "band_gap",
    "eta": "efficiency", "\u03b7": "efficiency", "efficiency": "efficiency",
    "pce": "power_conversion_efficiency",
    "jc": "critical_current_density", "j_c": "critical_current_density",
    "hc2": "upper_critical_field", "hc1": "lower_critical_field",
    "mobility": "carrier_mobility", "mu": "symbol_mu_ambiguous",
    "capacity": "specific_capacity",
    "coercivity": "coercivity", "hc": "coercivity",
    "magnetization": "magnetization", "ms": "saturation_magnetization",
    "hardness": "hardness", "young's modulus": "youngs_modulus",
    "thermal conductivity": "thermal_conductivity",
    "kappa": "symbol_kappa_ambiguous",
    "insertion loss": "insertion_loss", "extinction ratio": "extinction_ratio",
    "quality factor": "quality_factor", "q factor": "quality_factor",
    "responsivity": "responsivity", "threshold voltage": "threshold_voltage",
    "turn-on voltage": "turn_on_voltage", "sheet resistance": "sheet_resistance",
    "carrier concentration": "carrier_concentration", "curie temperature": "curie_temperature",
    "on/off ratio": "on_off_ratio", "reflectance": "reflectance",
    "transmittance": "transmittance", "absorbance": "absorbance",
    "strain": "strain", "stress": "stress", "modulus": "youngs_modulus",
    "lifetime": "lifetime", "bandwidth": "bandwidth", "wavelength": "wavelength",
    "frequency": "frequency",
    "voltage": "voltage", "current": "current", "power": "power",
    "temperature": "temperature", "pressure": "pressure",
}

DIMENSION_OF_QUANTITY = {
    "critical_temperature": "temperature", "glass_transition_temperature": "temperature",
    "melting_temperature": "temperature", "neel_temperature": "temperature",
    "electrical_conductivity": "conductivity", "resistivity": "resistivity",
    "band_gap": "energy", "power_conversion_efficiency": "fraction",
    "critical_current_density": "current_density", "upper_critical_field": "magnetic_flux_density",
    "lower_critical_field": "magnetic_flux_density", "coercivity": "magnetic_field_strength",
    "thermal_conductivity": "power/(length*temperature)",
}

# conditions that QC expects for temperature-sensitive quantities
CONDITION_REQUIRED = {
    "electrical_conductivity": ["temperature"],
    "resistivity": ["temperature"],
    "seebeck_coefficient": ["temperature"],
    "specific_capacity": ["temperature", "current_density_or_rate"],
    "carrier_mobility": ["temperature"],
}

SYSTEM_PROMPT = """You extract quantitative materials-science data from scholarly papers.
STRICT RULES:
1. Only report values that appear VERBATIM in the provided text.
2. Every item MUST include "quote": an exact contiguous substring of the text
   containing the value, and "page": the page marker it appears under.
3. If a field is not stated, omit it or set it to null. NEVER estimate,
   interpolate, convert units, or fill plausible values.
4. Keep value_raw and unit_raw exactly as printed.
Return ONLY a JSON array of objects:
[{"sample": str|null, "quantity": str, "value_raw": str, "unit_raw": str|null,
  "conditions_raw": str|null, "method_raw": str|null,
  "quote": str, "page": int, "source_kind": "text"|"table"}]"""

LANGEXTRACT_PROMPT = ("Extract quantitative measurement records (sample, quantity, "
                      "value_raw, unit_raw, conditions) from this scientific paper text. "
                      "Every extraction must quote the exact source sentence.")

LANGEXTRACT_EXAMPLES = [
    {
        "text": "The sample La2-xSrxMnO3 with x=0.3 shows Tc = 250 K measured by SQUID at 0.1 T.",
        "extraction": {
            "measurement": [
                {"sample": "La2-xSrxMnO3 x=0.3", "quantity": "Tc",
                 "value_raw": "250", "unit_raw": "K",
                 "conditions_raw": "0.1 T, SQUID"},
            ],
        },
    },
]


def measurement_id() -> str:
    return new_id("M")


def now() -> str:
    return utc_now()
