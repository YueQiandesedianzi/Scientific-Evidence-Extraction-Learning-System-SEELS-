from seels.extraction.units import parse_value_unit as pv, standardize as st, unit_key


def test_plain_and_scientific():
    assert pv("93")["value"] == 93.0
    r = pv("1.2 \u00d7 10^3 S/cm")
    assert abs(r["value"] - 1200) < 1e-6 and r["unit"] == "S/cm"
    r = pv("2.5\u00d710\u207b\u00b3 A")
    assert abs(r["value"] - 0.0025) < 1e-9


def test_comma_policy():
    assert pv("0,5 V")["value"] == 0.5            # decimal comma
    assert pv("1,234 K")["value"] == 1234          # thousands separator
    r = pv("12,34,56")
    assert r["value"] is None and r["reason"] == "ambiguous_comma_number"


def test_ranges_and_uncertainty_never_guess():
    r = pv("93-95 K")
    assert r["value"] is None and r["reason"] == "range_not_single_value"
    r = pv("1.2 \u00b1 0.1 V")
    assert r["value"] == 1.2 and r["uncertainty"] == 0.1
    r = pv("~25 meV")
    assert r["value"] == 25 and r["approx"] and r["unit"] == "meV"


def test_standardization():
    assert st(93, "K")["value_std"] == 93
    assert abs(st(25, "\u00b0C")["value_std"] - 298.15) < 1e-9
    assert abs(st(1.2, "S/cm")["value_std"] - 120) < 1e-9
    assert abs(st(3.5, "\u03a9\u00b7cm")["value_std"] - 0.035) < 1e-12
    assert abs(st(0.0083, "Ohm*cm")["value_std"] - 8.3e-5) < 1e-15
    assert abs(st(11.57, "W/mK")["value_std"] - 11.57) < 1e-12


def test_unknown_units_are_null_with_reason():
    r = st(5, "furlong/fortnight")
    assert r["value_std"] is None and r["reason"].startswith("unknown_unit")
    r = st(5, "emu/g")
    assert r["value_std"] is None and "needs_density" in r["reason"]


def test_unit_key_normalization():
    from seels.extraction.units import UNITS
    assert unit_key("Ohm*cm") == "ohm*cm"
    assert unit_key("W/(m\u00b7K)") == unit_key("W/m*K")
    # different spellings of Ohm*cm must resolve to the same registry entry
    assert UNITS[unit_key("\u03a9\u00b7cm")] == UNITS[unit_key("Ohm*cm")]
    assert UNITS[unit_key("mW/(m\u00b7K)")] == UNITS[unit_key("W/mK")][0:2] + (1e-3,)
