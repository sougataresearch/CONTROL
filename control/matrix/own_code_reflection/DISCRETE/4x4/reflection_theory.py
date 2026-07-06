"""Theoretical reflection Mueller matrix for a flat, isotropic sample:
either a bare substrate (single interface) or a substrate with one thin
film on top (two interfaces, e.g. SiO2 on Si) -- the two cases discussed
for this project's reflection-mode work. Not applicable to a rough/
depolarizing/anisotropic sample; this models the ideal, coherent,
specular-reflection case only.

Physics, in three steps:

1. Fresnel reflection coefficients (r_s, r_p) at a single interface, for
   s- and p-polarized light -- fresnel_coefficients().
2. For a substrate with one film on top, the Airy summation formula
   combines the two interfaces' Fresnel coefficients with the coherent
   phase accumulated crossing the film -- this is the N=1 special case of
   the general transfer-matrix method (TMM); see airy_reflection() and its
   docstring for why a full multi-layer TMM wasn't built. Passing
   film_thickness_nm=0 (or film index equal to the substrate's) collapses
   this back to the bare single-interface case -- verified in
   test_reflection_theory.py.
3. Convert the resulting complex (r_p, r_s) into a 4x4 Mueller matrix via
   the standard Jones-to-Mueller formula for a diagonal Jones matrix (no
   p/s cross-coupling, true for an isotropic flat sample) --
   jones_diagonal_to_mueller().

Basis convention: p and s here are defined relative to the plane of
incidence, and the Mueller matrix returned is expressed in that (p, s)
basis -- S1 = p-vs-s linear polarization, S2 = +/-45 deg to that, same
convention mueller_forward_model.py already uses for the PSG/PSA optics.
Whatever your PSG/PSA "0 degrees" physically corresponds to on the bench
must be aligned with the p-axis (in the plane of incidence) for a
comparison against this theoretical matrix to be meaningful -- this module
has no way to detect or correct a frame mismatch; see the own_code_reflection
README for the physical alignment discussion.
"""

from __future__ import annotations

import numpy as np


def _complex_index(n: float, k: float) -> complex:
    """Complex refractive index n - i*k (k >= 0 is absorption), the
    standard optics convention."""

    return complex(n, -k)


def fresnel_coefficients(n_incident: complex, n_transmitted: complex, theta_incident_deg: float):
    """Complex Fresnel reflection coefficients (r_s, r_p) at a single
    interface, for light going from n_incident to n_transmitted at angle
    theta_incident_deg (in the n_incident medium). Works for absorbing
    media (complex n_transmitted) via a complex cos(theta_transmitted) from
    Snell's law -- no explicit complex angle needed."""

    theta_i = np.deg2rad(theta_incident_deg)
    cos_i = np.cos(theta_i)
    sin_i = np.sin(theta_i)

    sin_t = (n_incident / n_transmitted) * sin_i
    cos_t = np.sqrt(1.0 - sin_t ** 2 + 0j)  # complex sqrt, principal branch

    r_s = (n_incident * cos_i - n_transmitted * cos_t) / (n_incident * cos_i + n_transmitted * cos_t)
    r_p = (n_transmitted * cos_i - n_incident * cos_t) / (n_transmitted * cos_i + n_incident * cos_t)
    return r_s, r_p


def airy_reflection(n0: complex, n1: complex, n2: complex, theta0_deg: float,
                     wavelength_nm: float, film_thickness_nm: float):
    """Total (r_s, r_p) for light in medium n0, hitting a film of index n1
    and thickness film_thickness_nm, sitting on a substrate of index n2, at
    angle theta0_deg in medium n0. The Airy summation formula -- the
    closed-form solution for exactly one film layer, rather than the
    general N-layer transfer-matrix method, since this project's two
    stated cases (bare substrate, substrate + one film) never need more
    than one layer; see the root README's TMM discussion for the general
    multi-layer formula this specializes."""

    r01_s, r01_p = fresnel_coefficients(n0, n1, theta0_deg)
    r12_s, r12_p = fresnel_coefficients(n1, n2, _angle_in_medium_deg(n0, n1, theta0_deg))

    cos_theta1 = _cos_from_snell(n0, n1, theta0_deg)
    beta = 2 * np.pi * film_thickness_nm * n1 * cos_theta1 / wavelength_nm
    phase = np.exp(-2j * beta)

    r_s = (r01_s + r12_s * phase) / (1 + r01_s * r12_s * phase)
    r_p = (r01_p + r12_p * phase) / (1 + r01_p * r12_p * phase)
    return r_s, r_p


def _cos_from_snell(n_incident: complex, n_transmitted: complex, theta_incident_deg: float) -> complex:
    theta_i = np.deg2rad(theta_incident_deg)
    sin_t = (n_incident / n_transmitted) * np.sin(theta_i)
    return np.sqrt(1.0 - sin_t ** 2 + 0j)


def _angle_in_medium_deg(n_incident: complex, n_transmitted: complex, theta_incident_deg: float) -> float:
    """Real-valued angle (degrees) to feed back into fresnel_coefficients()
    for the second interface -- only the cosine (computed separately via
    _cos_from_snell, which stays fully complex) is used for anything
    physically load-bearing; this angle is a bookkeeping convenience so
    fresnel_coefficients() can be reused unchanged for the second
    interface."""

    theta_i = np.deg2rad(theta_incident_deg)
    sin_t = (n_incident / n_transmitted) * np.sin(theta_i)
    return float(np.degrees(np.real(np.arcsin(np.clip(sin_t.real, -1.0, 1.0)))))


def jones_diagonal_to_mueller(r_p: complex, r_s: complex) -> np.ndarray:
    """4x4 Mueller matrix for a diagonal Jones matrix diag(r_p, r_s) in the
    (p, s) basis -- valid for an isotropic flat sample with no p/s
    cross-coupling. Standard Jones-to-Mueller conversion (Azzam & Bashara)."""

    rp2 = abs(r_p) ** 2
    rs2 = abs(r_s) ** 2
    cross = r_p * np.conj(r_s)

    return 0.5 * np.array([
        [rp2 + rs2, rp2 - rs2, 0.0, 0.0],
        [rp2 - rs2, rp2 + rs2, 0.0, 0.0],
        [0.0, 0.0, 2 * cross.real, 2 * cross.imag],
        [0.0, 0.0, -2 * cross.imag, 2 * cross.real],
    ], dtype=np.float64)


def bare_substrate_mueller(substrate_n: float, substrate_k: float,
                            angle_of_incidence_deg: float) -> np.ndarray:
    """Theoretical 4x4 reflection Mueller matrix for a bare substrate
    (single interface, air -> substrate), normalized so M[0,0]=1."""

    n0 = complex(1.0, 0.0)
    n_sub = _complex_index(substrate_n, substrate_k)
    r_s, r_p = fresnel_coefficients(n0, n_sub, angle_of_incidence_deg)
    m = jones_diagonal_to_mueller(r_p, r_s)
    return m / m[0, 0]


def film_on_substrate_mueller(substrate_n: float, substrate_k: float,
                               film_n: float, film_k: float, film_thickness_nm: float,
                               angle_of_incidence_deg: float, wavelength_nm: float) -> np.ndarray:
    """Theoretical 4x4 reflection Mueller matrix for a substrate with one
    thin film on top (e.g. SiO2 on Si), normalized so M[0,0]=1."""

    n0 = complex(1.0, 0.0)
    n_film = _complex_index(film_n, film_k)
    n_sub = _complex_index(substrate_n, substrate_k)
    r_s, r_p = airy_reflection(n0, n_film, n_sub, angle_of_incidence_deg, wavelength_nm, film_thickness_nm)
    m = jones_diagonal_to_mueller(r_p, r_s)
    return m / m[0, 0]
