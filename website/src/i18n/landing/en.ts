/**
 * Landing page strings — English, the reference every locale must match key for key.
 *
 * Build-time only: the landing renders each locale to static HTML, so none of this
 * ships as JavaScript. Values may hold trusted inline HTML (<em>, <strong>, <code>,
 * <a>, <p>). `{base}` becomes the locale's path prefix ("/en", "/ru", ...).
 */
export const en = {
  // Page metadata
  'page.title': 'Meridian — Self-Hosted VPN, Deployed Right',
  'page.description':
    'One command deploys a hardened VLESS+Reality VPN server with a built-in web panel and a link to share. Open source.',
  'page.ogAlt': 'Meridian: Your own VPN. A link to share.',

  // Hero
  'hero.kicker': 'Open source / Self-hosted',
  'hero.title': 'Your own VPN. <em>A link to share.</em>',
  'hero.sub': 'Meridian sets up a VPN on your server that can blend in with ordinary web traffic. Each person you invite gets a private connection link.',
  'hero.cta': 'Set up Meridian',
  'hero.demo': 'Try the demos',
  'hero.docs': 'Read the docs',
  'hero.pause': 'Pause globe animation',
  'hero.replay': 'Replay globe animation',
  'hero.resume': 'Resume globe animation',
  'hero.figure': 'Fig. 01 / Connection atlas',
  'hero.server': 'Server',
  'hero.device': 'Device',
  'hero.mapAlt': 'Illustrated globe with three devices linked to one private server.',

  // Server requirements and project license
  'spec.server': 'Server OS',
  'spec.serverValue': 'Debian or Ubuntu',
  'spec.protocols': 'Connections',
  'spec.license': 'License',
  'spec.licenseValue': 'Open source (MIT)',

  // How it works
  'setup.label': 'Installation',
  'setup.title': 'From a server to a VPN.',
  'setup.desc': 'Start with a fresh Debian or Ubuntu VPS and a terminal on your computer. Run the installer, then let the setup wizard guide you.',
  'setup.step1.label': '01 / Install the CLI',
  'setup.step2.label': '02 / Start the wizard',
  'setup.hint': 'The wizard shows you the plan before it changes your server. <a href="{base}/getting-started/">Read the setup guide</a>.',
  'setup.video':
    'A full <code>meridian setup</code>, start to finish: 3½ minutes. <a href="/video/meridian-setup.mp4" download>Download the video</a>',
  'setup.videoLabel': 'Video: setting up a personal VPN with Meridian',
  'setup.details': 'What happens behind the scenes',
  'setup.idempotent':
    'Safe to re-run. A second run keeps credentials and keys, and resumes or updates the deployment in place.',

  // Behind-the-scenes timeline (title, then the text that follows it)
  'step.1.title': 'Installs the control service and web panel',
  'step.1.text': ' with a node agent that runs <strong>Xray</strong> — no Docker, no third-party panel',
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
  'builder.title': 'Build your command',
  'builder.manual': 'Read the CLI reference: commands, options, and examples',
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
    'Deploy Reality, XHTTP, and Hysteria2 on one server. Sets up the control service, web panel, nginx, the firewall, and TLS.',
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
  'tech.label': 'Technical notes',
  'tech.title': 'Inside the network.',
  'tech.desc': 'See how connections reach your server and what someone observing the traffic can see.',
  'tech.group': 'What the network sees',
  'tech.dpi': 'Deep Packet Inspection (DPI)',
  'tech.dpi.body':
    '<p>DPI inspects traffic patterns to identify VPN and proxy protocols. OpenVPN and WireGuard have handshakes that are easy to recognize and block.</p><p><strong>VLESS+Reality</strong> runs inside a standard TLS 1.3 connection on port 443, so DPI sees what looks like ordinary HTTPS to the camouflage site. Every server also offers XHTTP and Hysteria2, so a block on one method leaves the others working.</p>',
  'tech.probe': 'Active probing',
  'tech.probe.body':
    '<p>Censors connect to suspicious servers and try to fingerprint them. If a server responds differently than the website it claims to be, it gets blocked.</p><p><strong>Reality</strong> borrows the TLS identity of a real website: by default a site hosted near your server, or <code>www.google.com</code>. A probe without your key is passed through to that site and sees its genuine certificate. Only clients with your key enter the VPN tunnel.</p>',
  'tech.fp': 'TLS fingerprinting',
  'tech.fp.body':
    '<p>Every TLS client sends a unique "Client Hello" fingerprint. Censors flag connections where the fingerprint doesn\'t match the claimed application.</p><p>Client apps use <strong>uTLS</strong> to send the same Client Hello as Chrome. Meridian also publishes a Firefox copy of each Reality and XHTTP entry, so if censors flag one fingerprint, the other keeps working.</p>',

  // Showcase
  'showcase.label': 'Product tour',
  'showcase.title': 'Two views of your VPN.',
  'showcase.desc': 'Try both demos with sample data. You can add a person in the console or follow their setup steps on the connection page.',
  'showcase.page.role': 'For the people you invite',
  'showcase.console.role': 'For the person running the server',
  'showcase.page.title': 'Connection page',
  'showcase.page.text': 'Open a personal link, pick an app, and add the VPN in one tap. The same page shows usage and when access expires.',
  'showcase.page.try': 'Open the connection demo',
  'showcase.console.title': 'Owner console',
  'showcase.console.text': 'Invite people, share QR codes, and set limits on time or traffic. Keep an eye on your servers from the same panel.',
  'showcase.console.try': 'Open the console demo',

  // Connect
  'connect.title': 'Which apps can I use?',
  'connect.apps.more':
    'Connection pages list these apps first and link others for each platform. <a href="/demo/?lang=en">See the demo</a>.',
  'connect.clock':
    'Turn on automatic date and time on every device. Connections can fail when the device clock is wrong.',

  // Architecture
  'arch.title': 'Server configurations',
  'arch.figure': 'View the server diagram',
  'arch.docs': 'Read the architecture guide',
  'arch.alt':
    'Meridian architecture: nginx routes TCP 443 by SNI to Xray Reality, XHTTP, and the connection pages; Hysteria2 listens on UDP 443',
  'arch.standalone': 'Standalone mode (no domain)',
  'arch.standalone.body':
    "<p><strong>nginx</strong> owns TCP port 443 and routes connections by TLS SNI with its stream module. Reality connections go to <strong>Xray</strong>. Everything else reaches nginx's http module, which serves the connection pages, subscriptions, and XHTTP over HTTPS with a short-lived Let's Encrypt certificate for the server's IP (issued by acme.sh). <strong>Hysteria2</strong> listens on UDP 443 with salamander obfuscation.</p><p>Meridian's <strong>control service</strong> sits behind nginx on a secret path: it keeps your users, serves their subscriptions, and hosts the web panel over HTTPS, with no SSH tunnel needed. A small agent on each server dials out to it, so nodes open no extra ports.</p>",
  'arch.domain': 'Domain mode (CDN fallback)',
  'arch.domain.body':
    "<p>With <code>--domain</code>, nginx gets a Let's Encrypt certificate for your domain and routes that SNI to the domain's paths. XHTTP moves to the domain and <strong>VLESS+WSS</strong> is added. With Cloudflare proxying the domain, both travel through the CDN and keep working if your server's IP is blocked. The control service and connection pages stay on the IP certificate.</p>",
  'arch.relay': 'Relay mode (domestic entry point)',
  'arch.relay.body':
    '<p>A <strong>Realm</strong> TCP forwarder on a server inside your country passes connections to your exit abroad. Relays carry Reality only; XHTTP, WSS, and Hysteria2 still connect to the exit directly. Encryption runs end to end between the app and the exit, so the relay never sees plaintext.</p><p>Add one with <code>meridian relay add RELAY_IP --exit EXIT</code>. Subscriptions and connection pages pick up the relay entries automatically.</p>',

  // Closing call to action
  'cta.title': 'Start with one server.',
  'cta.desc': 'Bring your first server online, then send someone their connection link. You can add more people or servers at any time.',
} as const;

export type LandingKey = keyof typeof en;
export type LandingStrings = Record<LandingKey, string>;
