/**
 * Landing page strings — English, the reference every locale must match key for key.
 *
 * Build-time only: the landing renders each locale to static HTML, so none of this
 * ships as JavaScript. Values may hold trusted inline HTML (<em>, <strong>, <code>,
 * <a>, <p>). `{base}` becomes the locale's path prefix ("/en", "/ru", ...).
 */
export const en = {
  // Page metadata
  'page.title': 'Meridian — Censorship-Resistant Proxy, Deployed Right',
  'page.description':
    'One command deploys a hardened VLESS+Reality proxy server and a link to share. Open source.',
  'page.ogAlt': 'Meridian: share secure internet with the people you care about',

  // Hero
  'hero.kicker': 'Open-source internet freedom tool',
  'hero.title': 'Share <em>secure internet</em> with the people you&nbsp;care&nbsp;about',
  'hero.sub':
    'One command sets up a proxy server that is hard to tell apart from ordinary HTTPS, with the firewall, TLS, and routing configured for you. Share access with a link or QR code.',
  'hero.cta': 'Get started',
  'hero.docs': 'Read the docs',
  'hero.previewAlt': 'Connection page — what your friends receive',
  'hero.caption': 'What your friends receive — <a href="/demo/">see the demo</a>',

  // How it works
  'setup.label': 'How it works',
  'setup.title': 'Two commands to a working proxy',
  'setup.desc':
    'You need a Debian or Ubuntu VPS (a $5/month one is enough) and a terminal on your computer. Meridian sets up the rest.',
  'setup.step1.label': 'Step 1 — Install',
  'setup.step2.label': 'Step 2 — Set up',
  'setup.then': 'then',
  'setup.hint':
    'The wizard asks a few plain questions, shows a review, then sets up your server. For one server with no questions, run <code>meridian deploy IP</code> — see the <a href="{base}/cli-reference/">CLI reference</a>.',
  'setup.terminalAlt': 'Meridian deploy output in a terminal',
  'setup.video':
    'A full <code>meridian setup</code>, start to finish: 3½ minutes. <a href="/video/meridian-setup.mp4" download>Download the video</a>',
  'setup.videoLabel': 'Video: setting up a personal VPN with Meridian',
  'setup.details': 'What happens behind the scenes',
  'setup.idempotent':
    'Safe to re-run. A second run keeps credentials and keys, and resumes or updates the deployment in place.',

  // Behind-the-scenes timeline (title, then the text that follows it)
  'step.1.title': 'Installs the control service',
  'step.1.text': ' and a node agent that runs <strong>Xray</strong> — no Docker, no third-party panel',
  'step.2.title': 'Generates x25519 keypair',
  'step.2.text': ' — unique encryption keys for Reality authentication',
  'step.3.title': 'Hardens the server',
  'step.3.text':
    ' — UFW opens only your SSH port, 443 (TCP and UDP), and 80 for certificates; SSH key-only login, fail2ban, <strong>BBR</strong> congestion control',
  'step.4.title': 'Configures VLESS+Reality',
  'step.4.text': " on TCP 443, where probes see a real website's certificate",
  'step.5.title': 'Adds XHTTP and Hysteria2',
  'step.5.text': ' — XHTTP through nginx on the same port, Hysteria2 on UDP 443 with salamander obfuscation',
  'step.6.title': 'Outputs QR codes',
  'step.6.text': ' and a connection page once real traffic has passed through the new subscription',

  // Command builder section
  'builder.label': 'Command builder',
  'builder.title': 'Build your command',
  'builder.desc':
    'Pick options and copy the full command. Covers deploy, relays, checks, camouflage scans, diagnostics, and teardown.',

  // Command builder widget
  'builder.tab.deploy': 'Deploy',
  'builder.tab.relay': 'Relay',
  'builder.tab.check': 'Check',
  'builder.tab.scan': 'SNI scan',
  'builder.tab.doctor': 'Doctor',
  'builder.tab.teardown': 'Teardown',
  'builder.desc.deploy':
    'Deploy Reality, XHTTP, and Hysteria2 on one server. Sets up the control service, nginx, the firewall, and TLS.',
  'builder.desc.relay':
    'Add a TCP relay on a domestic server that forwards to an exit abroad. The exit must be deployed first.',
  'builder.desc.check':
    'Check whether a new server can run Meridian (SNI, ports, DNS, OS, disk), or whether a deployed one is healthy.',
  'builder.desc.scan':
    "Find camouflage (SNI) sites on your server's own network with RealiTLScanner, then pick one.",
  'builder.desc.doctor': 'Collect a diagnostic report for bug reports. Secrets are redacted.',
  'builder.desc.teardown':
    'Remove Meridian from a server. A single-server deployment is removed completely, including the local configuration.',
  'builder.field.server': 'Server',
  'builder.field.server.placeholder': '198.51.100.10 or local',
  'builder.field.server.hint':
    'Server IP, a saved server name, or "local" to run on the server itself. Leave empty to be asked.',
  'builder.field.relayServer': 'Relay server',
  'builder.field.relayServer.hint': 'IP of the domestic server that will forward traffic.',
  'builder.field.user': 'SSH user',
  'builder.field.user.hint': 'Default: the saved user, else root. Non-root users need passwordless sudo.',
  'builder.field.sshPort': 'SSH port',
  'builder.field.sshPort.hint': 'Default: the saved port, else 22.',
  'builder.field.exit': 'Exit server',
  'builder.field.exit.placeholder': '198.51.100.10 or server name',
  'builder.field.exit.hint': 'IP or name of the deployed exit this relay forwards to.',
  'builder.field.domain': 'Domain',
  'builder.field.domain.hint':
    'Optional. Moves XHTTP to your domain and adds WSS; both can run through the Cloudflare CDN.',
  'builder.field.sni': 'Camouflage site (SNI)',
  'builder.field.sni.placeholder': 'auto',
  'builder.field.sni.hint':
    'The site Reality imitates. Leave it on auto and deploy picks one that works from your server, or find one with the SNI scan tab.',
  'builder.field.relaySni.hint': "Leave on auto to inherit the exit's camouflage site.",
  'builder.field.clientName': 'First client',
  'builder.field.clientName.hint': 'Name for the first person who connects. Default: "default".',
  'builder.field.displayName': 'Page title',
  'builder.field.displayName.placeholder': "Alice's VPN",
  'builder.field.displayName.hint': 'Label shown on connection pages.',
  'builder.field.icon': 'Page icon',
  'builder.field.icon.hint': 'An emoji or an image URL for the connection page.',
  'builder.field.color': 'Page color',
  'builder.field.color.hint': 'Color theme of the connection page.',
  'builder.field.color.default': 'Default (ocean)',
  'builder.palette.ocean': 'Ocean',
  'builder.palette.sunset': 'Sunset',
  'builder.palette.forest': 'Forest',
  'builder.palette.lavender': 'Lavender',
  'builder.palette.rose': 'Rose',
  'builder.palette.slate': 'Slate',
  'builder.field.relayName': 'Relay name',
  'builder.field.relayName.hint': 'Optional friendly name, such as "ru-moscow".',
  'builder.field.port': 'Listen port',
  'builder.field.port.hint': 'Port the relay listens on. Default: 443.',
  'builder.field.harden': 'Leave SSH and firewall alone',
  'builder.field.harden.hint': 'Only when other services share this server; hardening is on by default.',
  'builder.field.ai': 'AI-ready output',
  'builder.field.ai.hint': 'Copies a prompt for ChatGPT or Claude to the clipboard.',
  'builder.field.yes': 'Skip confirmation',
  'builder.field.yes.hint': 'For scripts and non-interactive SSH sessions.',
  'builder.command': 'Command',
  'builder.copy': 'Copy',
  'builder.copied': 'Copied',
  'builder.wizard':
    '<strong>First time?</strong> Just run <code>meridian setup</code> — the wizard asks for everything it needs.',
  'builder.status.invalid': 'Use an IP address, a saved server name, or "local".',
  'builder.status.local': 'Runs on the server itself without SSH, as root or with passwordless sudo.',
  'builder.status.deploy': 'Deploys Reality, XHTTP, and Hysteria2.',
  'builder.status.deployDomain': 'Deploys Reality, XHTTP, and Hysteria2, plus WSS on your domain.',
  'builder.status.relayNeed': 'Enter the relay server and the exit it forwards to.',
  'builder.status.relayOk': 'Adds Reality entries that enter through the relay; other methods still connect to the exit.',
  'builder.status.checkNeed': 'With no server, checks the whole saved fleet.',
  'builder.status.checkOk': 'Runs checks without changing anything.',
  'builder.status.scanNeed': 'Enter your server to find camouflage sites near it.',
  'builder.status.scanOk': 'Scans the server’s own network, lets you pick a site, and prints the deploy command.',
  'builder.status.doctorNeed': 'With no server, reports on the saved one.',
  'builder.status.doctorOk': 'Collects a redacted report. Turn on AI-ready output for a ready-made prompt.',
  'builder.status.doctorAi': 'Collects a redacted report and copies an AI-ready prompt to the clipboard.',
  'builder.status.teardownNeed': 'With no server, uses the saved one when there is only one.',
  'builder.status.teardownOk': 'In a fleet, remove a server’s role with meridian node remove first.',

  // How it avoids detection
  'tech.label': 'Technology',
  'tech.title': 'How it avoids detection',
  'tech.desc':
    'OpenVPN and WireGuard have recognizable traffic signatures. A VLESS+Reality connection is hard to tell apart from ordinary HTTPS to a real website.',
  'tech.dpi': 'Deep Packet Inspection (DPI)',
  'tech.dpi.body':
    '<p>DPI analyzes traffic patterns to identify proxy protocols. OpenVPN and WireGuard have handshakes that are easy to recognize and block.</p><p><strong>VLESS+Reality</strong> runs inside a standard TLS 1.3 connection on port 443, so DPI sees what looks like ordinary HTTPS to the camouflage site. Every server also offers XHTTP and Hysteria2, so a block on one method leaves the others working.</p>',
  'tech.probe': 'Active probing',
  'tech.probe.body':
    '<p>Censors connect to suspicious servers and try to fingerprint them. If a server responds differently than the website it claims to be, it gets blocked.</p><p><strong>Reality</strong> borrows the TLS identity of a real website: by default a site hosted near your server, or <code>www.google.com</code>. A probe without your key is passed through to that site and sees its genuine certificate. Only clients with your key get the proxy tunnel.</p>',
  'tech.fp': 'TLS fingerprinting',
  'tech.fp.body':
    '<p>Every TLS client sends a unique "Client Hello" fingerprint. Censors flag connections where the fingerprint doesn\'t match the claimed application.</p><p>Client apps use <strong>uTLS</strong> to send the same Client Hello as Chrome. Meridian also publishes a Firefox copy of each Reality and XHTTP entry, so if censors flag one fingerprint, the other keeps working.</p>',

  // Connect
  'connect.label': 'Connect',
  'connect.title': 'Scan and go',
  'connect.desc':
    'After deploying, you get a connection page with QR codes. Send it to whoever needs it — they tap once and connect.',
  'connect.apps.more':
    'Connection pages list these apps first and link others for each platform. <a href="/demo/">See the demo</a>.',
  'connect.clock':
    'Turn on automatic date and time on every device. Connections can fail when the device clock is wrong.',

  // Architecture
  'arch.label': 'Architecture',
  'arch.title': 'Under the hood',
  'arch.alt':
    'Meridian architecture: nginx routes TCP 443 by SNI to Xray Reality, XHTTP, and the connection pages; Hysteria2 listens on UDP 443',
  'arch.standalone': 'Standalone mode (no domain)',
  'arch.standalone.body':
    "<p><strong>nginx</strong> owns TCP port 443 and routes connections by TLS SNI with its stream module. Reality connections go to <strong>Xray</strong>. Everything else reaches nginx's http module, which serves the connection pages, subscriptions, and XHTTP over HTTPS with a short-lived Let's Encrypt certificate for the server's IP (issued by acme.sh). <strong>Hysteria2</strong> listens on UDP 443 with salamander obfuscation.</p><p>Meridian's <strong>control service</strong> sits behind nginx on a secret path: it keeps your users, serves their subscriptions, and hosts a web console reachable over HTTPS, no SSH tunnel needed. A small agent on each server dials out to it, so nodes open no extra ports.</p>",
  'arch.domain': 'Domain mode (CDN fallback)',
  'arch.domain.body':
    "<p>With <code>--domain</code>, nginx gets a Let's Encrypt certificate for your domain and routes that SNI to the domain's paths. XHTTP moves to the domain and <strong>VLESS+WSS</strong> is added. With Cloudflare proxying the domain, both travel through the CDN and keep working if your server's IP is blocked. The control service and connection pages stay on the IP certificate.</p>",
  'arch.relay': 'Relay mode (domestic entry point)',
  'arch.relay.body':
    '<p>A <strong>Realm</strong> TCP forwarder on a server inside your country passes connections to your exit abroad. Relays carry Reality only; XHTTP, WSS, and Hysteria2 still connect to the exit directly. Encryption runs end to end between the app and the exit, so the relay never sees plaintext.</p><p>Add one with <code>meridian relay add RELAY_IP --exit EXIT</code>. Subscriptions and connection pages pick up the relay entries automatically.</p>',

  // Closing call to action
  'cta.title': 'Ready to get started?',
  'cta.desc':
    'Install Meridian, deploy to your server, and share access with the people who need it. A deploy usually takes 3 to 10 minutes.',
  'cta.install': 'Install Meridian',
} as const;

export type LandingKey = keyof typeof en;
export type LandingStrings = Record<LandingKey, string>;
