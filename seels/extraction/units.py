"""Value/unit parsing and standardization.

Honesty rules:
- Anything ambiguous (ranges, ambiguous decimal commas, unknown units,
  mass-normalized magnetic units needing density) yields value_std=None plus
  an explicit reason. We never guess a number.
- Raw value and raw unit are always preserved by the caller; this module only
  fills the standardized fields.
"""
from __future__ import annotations

import math
import re
import unicodedata

SUPER = {"\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4",
         "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
         "\u207a": "+", "\u207b": "-"}
SUB = {"\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
       "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9"}

# unit_key -> (dimension, canonical_unit, factor) ; key: NFKC-lowercased, spaces removed
UNITS = {
    # length -> m
    "m": ("length", "m", 1.0), "cm": ("length", "m", 0.01), "mm": ("length", "m", 1e-3),
    "um": ("length", "m", 1e-6), "nm": ("length", "m", 1e-9), "pm": ("length", "m", 1e-12),
    "km": ("length", "m", 1e3), "\u00e5": ("length", "m", 1e-10),
    "angstrom": ("length", "m", 1e-10),
    # temperature -> K  (Celsius handled as affine special case)
    "k": ("temperature", "K", 1.0),
    "\u00b0c": ("temperature_celsius", "K", "affine"), "degc": ("temperature_celsius", "K", "affine"),
    "\u2103": ("temperature_celsius", "K", "affine"),
    # pressure -> Pa
    "pa": ("pressure", "Pa", 1.0), "kpa": ("pressure", "Pa", 1e3),
    "mpa": ("pressure", "Pa", 1e6), "gpa": ("pressure", "Pa", 1e9),
    "bar": ("pressure", "Pa", 1e5), "mbar": ("pressure", "Pa", 1e2),
    "atm": ("pressure", "Pa", 101325.0), "torr": ("pressure", "Pa", 133.322368),
    "psi": ("pressure", "Pa", 6894.757),
    # energy -> J
    "j": ("energy", "J", 1.0), "kj": ("energy", "J", 1e3), "mj": ("energy", "J", 1e-3),
    "ev": ("energy", "J", 1.602176634e-19), "mev": ("energy", "J", 1.602176634e-22),
    "kev": ("energy", "J", 1.602176634e-16),
    "ry": ("energy", "J", 2.179872e-18), "hartree": ("energy", "J", 4.359744e-18),
    # power -> W
    "w": ("power", "W", 1.0), "mw": ("power", "W", 1e-3), "kw": ("power", "W", 1e3),
    "uw": ("power", "W", 1e-6), "nw": ("power", "W", 1e-9),
    # time -> s
    "s": ("time", "s", 1.0), "ms": ("time", "s", 1e-3), "us": ("time", "s", 1e-6),
    "ns": ("time", "s", 1e-9), "ps": ("time", "s", 1e-12),
    "min": ("time", "s", 60.0), "h": ("time", "s", 3600.0),
    # frequency -> Hz
    "hz": ("frequency", "Hz", 1.0), "khz": ("frequency", "Hz", 1e3),
    "mhz": ("frequency", "Hz", 1e6), "ghz": ("frequency", "Hz", 1e9),
    "thz": ("frequency", "Hz", 1e12),
    # electricity
    "a": ("current", "A", 1.0), "ma": ("current", "A", 1e-3), "ua": ("current", "A", 1e-6),
    "ka": ("current", "A", 1e3),
    "v": ("voltage", "V", 1.0), "mv": ("voltage", "V", 1e-3),
    "uv": ("voltage", "V", 1e-6), "kv": ("voltage", "V", 1e3),
    "\u03c9": ("resistance", "Ohm", 1.0), "ohm": ("resistance", "Ohm", 1.0),
    "m\u03c9": ("resistance", "Ohm", 1e-3), "k\u03c9": ("resistance", "Ohm", 1e3),
    "m\u03c9\u00b7cm": ("resistivity", "Ohm*m", 1e-2), "ohm*cm": ("resistivity", "Ohm*m", 1e-2),
    "ohmcm": ("resistivity", "Ohm*m", 1e-2), "\u03c9cm": ("resistivity", "Ohm*m", 1e-2),
    "\u03c9\u00b7cm": ("resistivity", "Ohm*m", 1e-2),
    "\u03bc\u03c9\u00b7cm": ("resistivity", "Ohm*m", 1e-8), "uohm*cm": ("resistivity", "Ohm*m", 1e-8),
    "\u03c9\u00b7m": ("resistivity", "Ohm*m", 1.0), "ohm*m": ("resistivity", "Ohm*m", 1.0),
    "s": None,  # placeholder removed below (Siemens conflicts with second 's')
    # conductance/conductivity (Siemens keyed with explicit forms)
    "siemens": ("conductance", "S", 1.0), "msiemens": ("conductance", "S", 1e-3),
    "s/cm": ("conductivity", "S/m", 100.0), "s\u00b7cm^-1": ("conductivity", "S/m", 100.0),
    "s/cm^3": None,
    "ms/cm": ("conductivity", "S/m", 0.1), "scm^-1": ("conductivity", "S/m", 100.0),
    "s/m": ("conductivity", "S/m", 1.0),
    "a/cm^2": ("current_density", "A/m^2", 1e4), "a/cm\u00b2": ("current_density", "A/m^2", 1e4),
    "ma/cm^2": ("current_density", "A/m^2", 10.0), "ma/cm\u00b2": ("current_density", "A/m^2", 10.0),
    "a/m^2": ("current_density", "A/m^2", 1.0), "a/m\u00b2": ("current_density", "A/m^2", 1.0),
    "mah/g": ("specific_capacity", "mAh/g", 1.0), "mahg^-1": ("specific_capacity", "mAh/g", 1.0),
    "c/g": ("specific_capacity_charge", "C/kg", 1e5),
    # magnetism
    "t": ("magnetic_flux_density", "T", 1.0), "mt": ("magnetic_flux_density", "T", 1e-3),
    "ut": ("magnetic_flux_density", "T", 1e-6), "kt": ("magnetic_flux_density", "T", 1e3),
    "oe": ("magnetic_field_strength", "A/m", 79.5774715),
    "emu/cm^3": ("magnetization_vol", "A/m", 1000.0), "emu/cm\u00b3": ("magnetization_vol", "A/m", 1000.0),
    "emu/g": ("magnetization_mass", None, "needs_density"),
    "\u03bcb": ("magnetic_moment", "J/T", 9.274010e-24),
    # amount / mass / molar
    "mol": ("amount", "mol", 1.0), "mmol": ("amount", "mol", 1e-3),
    "umol": ("amount", "mol", 1e-6), "nmol": ("amount", "mol", 1e-9),
    "g": ("mass", "kg", 1e-3), "mg": ("mass", "kg", 1e-6), "kg": ("mass", "kg", 1.0),
    "ug": ("mass", "kg", 1e-9), "ng": ("mass", "kg", 1e-12),
    "g/mol": ("molar_mass", "kg/mol", 1e-3), "gmol^-1": ("molar_mass", "kg/mol", 1e-3),
    "g\u00b7mol^-1": ("molar_mass", "kg/mol", 1e-3),
    # fractions
    "%": ("fraction", "1", 0.01), "wt%": ("mass_fraction", "1", 0.01),
    "at.%": ("atomic_fraction", "1", 0.01), "at%": ("atomic_fraction", "1", 0.01),
    "mol%": ("mole_fraction", "1", 0.01), "vol%": ("volume_fraction", "1", 0.01),
    "ppm": ("fraction", "1", 1e-6), "ppb": ("fraction", "1", 1e-9),
    # angles
    "\u00b0": ("angle", "rad", math.pi / 180.0), "deg": ("angle", "rad", math.pi / 180.0),
    "rad": ("angle", "rad", 1.0),
    # dimensionless
    "": ("dimensionless", "1", 1.0), "1": ("dimensionless", "1", 1.0),
    "a.u.": ("dimensionless", "1", 1.0), "au": ("dimensionless", "1", 1.0),
}
# resolve the "s" conflict: bare "s" is second (time); Siemens requires explicit forms.
UNITS["s"] = ("time", "s", 1.0)
UNITS.pop("s/cm^3", None)

# domain units common in physics/materials/optics papers
UNITS.update({
    "dB": ("log_ratio", "dB", 1.0),
    "dBm": ("log_power", "dBm", 1.0),
    "dB/cm": ("attenuation", "dB/m", 100.0),
    "dB/m": ("attenuation", "dB/m", 1.0),
    "dB/km": ("attenuation", "dB/m", 0.001),
    "A/W": ("responsivity", "A/W", 1.0),
    "V/cm": ("electric_field", "V/m", 100.0),
    "kV/cm": ("electric_field", "V/m", 1e5),
    "V/m": ("electric_field", "V/m", 1.0),
    "J/cm^2": ("fluence", "J/m^2", 1e4),
    "mJ/cm^2": ("fluence", "J/m^2", 10.0),
    "J/m^2": ("fluence", "J/m^2", 1.0),
    "W/(m*K)": ("thermal_conductivity", "W/(m*K)", 1.0),
    "W/m*K": ("thermal_conductivity", "W/(m*K)", 1.0),
    "mW/(m*K)": ("thermal_conductivity", "W/(m*K)", 1e-3),
    "mW/m*K": ("thermal_conductivity", "W/(m*K)", 1e-3),
    "K/W": ("thermal_resistance", "K/W", 1.0),
    "rpm": ("angular_velocity", "rad/s", 0.10471975512),
    "cm^-1": ("wavenumber", "1/m", 100.0),
    "s^-1": ("frequency", "Hz", 1.0),
    "mTorr": ("pressure", "Pa", 0.133322368),
    "uA/cm^2": ("current_density", "A/m^2", 0.01),
    "\u03c9*cm^2": ("resistance_area", "Ohm*m^2", 1e-4),
    "Coulomb": ("charge", "C", 1.0),
    "C/cm^2": ("charge_density", "C/m^2", 1e4),
    "mAh": ("charge_capacity", "C", 3.6),
    "\u03bcm": ("length", "m", 1e-6),
    "counts": ("counts", "counts", 1.0),
    "cycles": ("cycles", "cycles", 1.0),
    "kg/m^3": ("density", "kg/m^3", 1.0),
    "g/cm^3": ("density", "kg/m^3", 1000.0),
    "mg/cm^3": ("density", "kg/m^3", 1.0),
    "W/mK": ("thermal_conductivity", "W/(m*K)", 1.0),
    "mW/mK": ("thermal_conductivity", "W/(m*K)", 1e-3),
    "W m^-1 K^-1": ("thermal_conductivity", "W/(m*K)", 1.0),
    "mW m^-1 K^-1": ("thermal_conductivity", "W/(m*K)", 1e-3),
})

_NUM_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_SCI_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+))\s*(?:\u00d7|x|\*)\s*10\s*(?:\^|\*\*)?\s*([-+]?\d+)$",
                     re.IGNORECASE)


def _expand(text: str) -> str:
    out = []
    prev_super = False
    for ch in text:
        if ch in SUPER:
            if not prev_super:
                out.append("^")
            out.append(SUPER[ch])
            prev_super = True
        else:
            prev_super = False
            if ch in SUB:
                out.append(SUB[ch])
            else:
                out.append(ch)
    s = "".join(out)
    s = s.replace("\u00b5", "u").replace("\u03bc", "u")  # micro signs
    return unicodedata.normalize("NFKC", s)


def _resolve_comma(num: str):
    """Return (resolved_string|None, reason|None) for numbers containing commas."""
    if "," not in num:
        return num, None
    parts = num.split(",")
    if len(parts) == 2:
        a, b = parts
        if len(b) == 3 and a.isdigit() and len(a) <= 3 and "." not in num:
            return a + b, None            # thousands separator: 1,234
        if len(b) in (1, 2) and a.isdigit():
            return a + "." + b, None      # decimal comma: 0,5 / 1,25
    return None, "ambiguous_comma_number"


def parse_value_unit(text: str) -> dict:
    """Parse a raw cell/sentence fragment into value + unit.

    Returns dict(value=float|None, unit=str|None, uncertainty=float|None,
                 approx=bool, reason=str|None, normalized=str)
    Never invents numbers: on any ambiguity value=None with a reason.
    """
    if text is None:
        return {"value": None, "unit": None, "uncertainty": None, "approx": False,
                "reason": "empty", "normalized": ""}
    raw = str(text).strip()
    s = _expand(raw).strip()
    approx = False
    m = re.match(r"^[~\u2248\u2243]+\s*(.+)$", s)
    if m:
        approx = True
        s = m.group(1).strip()
    unc = None
    m = re.search(r"\+/-\s*([0-9.eE+-]+)|\u00b1\s*([0-9.eE+-]+)", s)
    if m:
        try:
            unc = float(m.group(1) or m.group(2))
            s = (s[:m.start()] + s[m.end():]).strip()
        except ValueError:
            pass
    s = s.strip()
    # ranges: keep raw, no single value
    if re.search(r"(?<![\deE])-|(?<=[\d\)])\s*(?:to|--|\u2013|\u2014)\s*", s) and \
            re.search(r"\d\s*(?:to|--|\u2013|\u2014)\s*\d", s) or re.search(r"\d+-\d+", s.replace("e-", "").replace("E-", "")):
        if re.search(r"\d\s*(?:-|to|--|\u2013|\u2014)\s*[-+]?\d", s):
            return {"value": None, "unit": _guess_unit_tail(s), "uncertainty": unc,
                    "approx": approx, "reason": "range_not_single_value", "normalized": s}
    # split number prefix vs unit tail
    m = re.match(r"^([-+]?[\d.,]+(?:\s*(?:\u00d7|x|\*)\s*10\s*(?:\^|\*\*)?\s*[-+]?\d+)?"
                 r"|[-+]?[\d.,]+[eE][-+]?\d+)?\s*(.*)$", s)
    num_part, unit_part = (m.group(1) or "").strip(), (m.group(2) or "").strip() if m else ("", s)
    value, reason = _to_float(num_part)
    unit = unit_part or None
    if unit:
        unit = re.sub(r"\s+", "", unit)
        unit = re.sub(r"[\[\]\(\)\.,:;]+$", "", unit) or None
    if value is not None and not math.isfinite(value):
        value, reason = None, "not_finite"
    return {"value": value, "unit": unit, "uncertainty": unc, "approx": approx,
            "reason": reason, "normalized": s}


def _guess_unit_tail(s: str):
    m = re.findall(r"[A-Za-z\u00b0\u03c9%/][A-Za-z\u00b0\u03c9%/\.\^\-\d]*\s*$", s)
    return m[-1].strip() if m else None


def _to_float(num_part: str):
    if not num_part:
        return None, "no_number"
    t = num_part.strip()
    m = _SCI_RE.match(t.replace(" ", ""))
    if m:
        try:
            return float(m.group(1)) * (10 ** int(m.group(2))), None
        except ValueError:
            return None, "unparsable_scientific"
    if "e" in t.lower() and _NUM_RE.match(t):
        return float(t), None
    resolved, reason = _resolve_comma(t)
    if reason:
        return None, reason
    if _NUM_RE.match(resolved or ""):
        return float(resolved), None
    return None, "unparsable_number"


def unit_key(unit: str) -> str:
    u = _expand(unit or "").strip().lower()
    u = re.sub(r"\s+", "", u)
    u = u.replace("**", "^").replace("\u00b7", "*").replace(".", "")
    u = u.replace("(", "").replace(")", "")
    u = re.sub(r"([a-z%])(\d)$", r"\1^\2", u)
    return u


# normalize all registry keys through unit_key so lookups always match
UNITS = {unit_key(k): v for k, v in UNITS.items()}


def standardize(value, unit: str) -> dict:
    """value(float|None) + raw unit -> standardized SI-ish value/unit.

    Returns dict(value_std, unit_std, method, reason). Unknown or ambiguous
    units yield value_std=None with reason (never a guessed conversion).
    """
    if value is None:
        return {"value_std": None, "unit_std": None, "method": None,
                "reason": "no_value_to_standardize"}
    key = unit_key(unit or "")
    entry = UNITS.get(key)
    if entry is None:
        return {"value_std": None, "unit_std": None, "method": None,
                "reason": "unknown_unit:" + (unit or "<empty>")}
    dim, canon, factor = entry
    if factor == "needs_density":
        return {"value_std": None, "unit_std": None, "method": None,
                "reason": "mass_normalized_unit_needs_density:" + unit}
    if factor == "affine":
        return {"value_std": value + 273.15, "unit_std": canon,
                "method": "affine:celsius_to_kelvin(+273.15)", "reason": None}
    return {"value_std": value * factor, "unit_std": canon,
            "method": "unit_table:%s->%s x%g" % (unit or "<none>", canon, factor),
            "reason": None}
