"""Materials Project field table — GENERATED, do not edit by hand.

Regenerate with: python tools/gen_mp_vocab.py  (source: 0.87.2.dev4+g6bd8ed856)

Maps each retrievable SummaryDoc property to its pinned unit (None =
dimensionless/count) and XC-functional origin (None = no traceable functional).
"""

MP_FIELDS: dict[str, dict] = {
    "band_gap": {"unit": "eV", "origin": "electronic_structure"},
    "bulk_modulus": {"unit": "GPa", "origin": None},
    "cbm": {"unit": "eV", "origin": "electronic_structure"},
    "density": {"unit": "g/cm³", "origin": "structure"},
    "density_atomic": {"unit": "Å³/atom", "origin": "structure"},
    "dos_energy_down": {"unit": "eV", "origin": "electronic_structure"},
    "dos_energy_up": {"unit": "eV", "origin": "electronic_structure"},
    "e_electronic": {"unit": None, "origin": "dielectric"},
    "e_ij_max": {"unit": "C/m²", "origin": "piezoelectric"},
    "e_ionic": {"unit": None, "origin": "dielectric"},
    "e_total": {"unit": None, "origin": "dielectric"},
    "efermi": {"unit": "eV", "origin": "electronic_structure"},
    "energy_above_hull": {"unit": "eV/atom", "origin": "energy"},
    "energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "equilibrium_reaction_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "formation_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "homogeneous_poisson": {"unit": None, "origin": None},
    "is_gap_direct": {"unit": None, "origin": "electronic_structure"},
    "is_magnetic": {"unit": None, "origin": "magnetism"},
    "is_metal": {"unit": None, "origin": "electronic_structure"},
    "is_stable": {"unit": None, "origin": "energy"},
    "n": {"unit": None, "origin": "dielectric"},
    "nelements": {"unit": None, "origin": None},
    "nsites": {"unit": None, "origin": "structure"},
    "num_magnetic_sites": {"unit": None, "origin": "magnetism"},
    "num_unique_magnetic_sites": {"unit": None, "origin": "magnetism"},
    "shape_factor": {"unit": None, "origin": None},
    "shear_modulus": {"unit": "GPa", "origin": None},
    "surface_anisotropy": {"unit": None, "origin": None},
    "total_magnetization": {"unit": "μB", "origin": "magnetism"},
    "total_magnetization_normalized_formula_units": {"unit": "μB/f.u.", "origin": "magnetism"},
    "total_magnetization_normalized_vol": {"unit": "μB/Å³", "origin": "magnetism"},
    "uncorrected_energy_per_atom": {"unit": "eV/atom", "origin": "energy"},
    "universal_anisotropy": {"unit": None, "origin": None},
    "vbm": {"unit": "eV", "origin": "electronic_structure"},
    "volume": {"unit": "Å³", "origin": "structure"},
    "weighted_surface_energy": {"unit": "J/m²", "origin": None},
    "weighted_surface_energy_EV_PER_ANG2": {"unit": "eV/Å²", "origin": None},
    "weighted_work_function": {"unit": "eV", "origin": None},
}
