"""
Energy and moisture are conserved if the following vertically integrated quantities are true:

    cp <Q1> = L_v Prec + SHF + RADSFC - RADTOP
    <Q2> = Evap - Prec

"""
from toolz import curry
import torch


def mass_integrate(x, w):
    return (x * w).sum(-1, keepdim=True)


def apply_linear_constraint(lin, a, x, *args, inequality=False, v=None,
                            **kwargs):
    """Apply a linear constraint

    Parameters
    ----------
    lin : Callable
        the linear constraint of the form lin(x, *args, **kwargs) = a that is linear in x
    inequality : bool
        assume that lin(x, *args, **kwargs) >= a

    Returns
    -------
    y : Tensor
      x transformed to satisfy the constraint

    """

    val_x = lin(x, *args, **kwargs)

    # use a constant adjustment
    # x - alpha * v
    if v is None:
        v = torch.ones(x.size(-1))
    val_v = lin(v, *args, **kwargs)
    alpha = (val_x - a)

    if inequality:
        alpha = alpha.clamp(max=0)

    return x - v * (alpha / val_v)


@curry
def expected_moisture(q0, fqt, precip, lhf, h, layer_mass):
    """Same as energy imbalance but for moisture budget

    Parameters
    ----------
    q0  (g/kg)
        moisture before
    fqt (g/kg/day)
        moistening from horizontal advection.
    h   (day)
        time step
    precip (mm/d)
        sfc flux over time step
    lhf  (W/m2)

    Returns
    -------
    current_pw, expected_pw (g/m2)

    """
    Lv = 2.51e6
    density_liquid = 1000.0

    water0 = mass_integrate(q0, layer_mass)/1000.0            # kg
    fqt_int = mass_integrate(fqt, layer_mass)/1000.0 / 86400  # kg/s
    net_evap = lhf / Lv - precip / 1000.0 * density_liquid / 86400     # kg/s
    h = h * 86400  # s
    return water0*1000, 1000 * (water0 + (fqt_int + net_evap) * h)


def precip_to_energy(prec):
    """Convert precip to W/m2

    Parameters
    ---------
    prec : (mm/day)
    """
    density_water = 1000
    Lv = 2.51e6
    coef = density_water / 1000 / 86400 * Lv
    return coef * prec


def expected_temperature(sl, fsl, prec, shf, radtoa, radsfc, h,
                         layer_mass):
    """Expected column average SLI to conserve energy

    Parameters
    ----------
    sl (K)
        output before neural network
    fsl (K/day)
        heat transport without surface fluxes
    prec (mm/day)
        change in precipitable water over time step
    shf, radtoa, radsfc (W/m2)
        surface and toa fields (upward)
    h (day)
        time step
    layer_mass (kg/m2)

    Returns
    -------
    sl0, sl_int (K / kg / m^2)
        integrals of before/after SLI

    """
    cp = 1004

    sl0 = mass_integrate(layer_mass, sl)
    fsl_int = mass_integrate(fsl, layer_mass)
    h_seconds = h * 86400

    energy_rate_of_change = precip_to_energy(prec) + shf + radsfc - radtoa
    sl1 = sl0 + fsl_int * h + energy_rate_of_change / cp * h_seconds
    return sl0, sl1


def enforce_expected_integral(x, int, layer_mass):
    int_actual = mass_integrate(x, layer_mass)
    return x * int/int_actual


def fix_negative_moisture(q, layer_mass):
    """Fix negative moisture in moisture conserving way"""
    eps = torch.tensor(1e-10, requires_grad=False)

    water_before = mass_integrate(q, layer_mass)
    q = q.clamp(eps)
    water_after = mass_integrate(q, layer_mass)
    return q * water_before/water_after


def apply_constraints(x0, x1, time_step):
    """Main function for applying constraints"""

    layer_mass = x0['layer_mass']
    qt = fix_negative_moisture(x1['qt'], x0['layer_mass'])
    if 'Prec' in x1:
        pw0, pw = expected_moisture(x0['qt'], x0['FQT'], x1['Prec'],
                                    x1['LHF'], time_step, x0['layer_mass'])

        sl_int0, sl_int = expected_temperature(x0['sl'], x0['FSL'], x1['Prec'],
                                               x1['SHF'], x1['RADTOA'],
                                               x1['RADSFC'], time_step,
                                               layer_mass)

        sl = enforce_expected_integral(x1['sl'], sl_int, layer_mass)
        qt = enforce_expected_integral(qt, pw, layer_mass)
    else:
        sl = x1['sl']

    x1['qt'] = qt
    x1['sl'] = sl
    return x1
