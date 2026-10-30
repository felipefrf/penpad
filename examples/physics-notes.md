# 2) Damped oscillator
A mass $m$ on a spring $k$ with viscous damping $c$:
$$ m\ddot{x} + c\dot{x} + kx = 0 $$
Try $x = e^{\lambda t}$, so the characteristic equation is
$$ m\lambda^2 + c\lambda + k = 0 \Rightarrow \lambda = \frac{-c \pm \sqrt{c^2 - 4mk}}{2m} $$
With $\omega_n = \sqrt{k/m}$ and $\zeta = \frac{c}{2\sqrt{km}}$:
$$! x(t) = A e^{-\zeta\omega_n t} \cos\left(\omega_d t - \phi\right) $$
(a) underdamped, $\zeta < 1$: oscillates with $\omega_d = \omega_n\sqrt{1 - \zeta^2}$
(b) critical, $\zeta = 1$: fastest return without overshoot
(c) overdamped, $\zeta > 1$: two real roots, slow decay
> Energy lost per cycle: $\Delta E = \oint c\dot{x}\,dx = \pi c \omega X^2$

## Numbers
{blue} $m = 2$ kg, $k = 800$ N/m, $c = 8$ N s/m
$$ \omega_n = \sqrt{\frac{800}{2}} = 20 \text{ rad/s}, \quad \zeta = \frac{8}{2\sqrt{1600}} = 0.1 $$
{red} Lightly damped: about $1/(2\pi\zeta) \approx 1.6$ cycles per e-fold decay.
