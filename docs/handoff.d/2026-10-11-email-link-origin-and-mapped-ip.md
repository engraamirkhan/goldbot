- Email sign-in link, security re-verify nits on 1a8bf43. `auth.mail.dashboard_origin` now refuses any
  whitespace, control character, `\` or `;`, and an empty host. It is stored as the normalised lower-case
  `scheme://netloc`. `coarse_ip` gives the IPv4 /16 for an IPv4-mapped IPv6 address (`::ffff:203.0.113.45` ->
  `203.0.x.x`).
