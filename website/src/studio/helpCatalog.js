export const studioJourneySteps = [
  {
    id: "welcome",
    label: "Prepare",
    short: "VPS basics",
    contextTitle: "Start with the provider page",
    contextBody: "Copy only the VPS details Studio needs. Keep the provider tab open.",
    contextItems: ["Public IP", "SSH username", "SSH port", "Initial password if needed"],
  },
  {
    id: "server",
    label: "Server",
    short: "Connection details",
    contextTitle: "Name one server first",
    contextBody: "Use a friendly title. Later screens refer to this server by name.",
    contextItems: ["Port 22 is the usual default", "Use the public internet IP", "Avoid private/internal IPs"],
  },
  {
    id: "connect",
    label: "Connect",
    short: "SSH validation",
    contextTitle: "Validate before deploy",
    contextBody: "Check access before changing anything on the server.",
    contextItems: ["Reachability", "Authentication", "Sudo access", "Operating system"],
  },
  {
    id: "keys",
    label: "Secure SSH",
    short: "Key setup",
    contextTitle: "Use the password once",
    contextBody: "Local Studio can install a key, then verify passwordless login.",
    contextItems: ["Private key stays local", "Public key goes on the server", "Key login is verified after install"],
  },
  {
    id: "deploy",
    label: "Configure",
    short: "Safe defaults",
    contextTitle: "Defaults first, advanced later",
    contextBody: "Start with a secure first deploy. Keep rare settings out of the way.",
    contextItems: ["Connection page name", "First client", "Hardening", "Optional routing later"],
  },
  {
    id: "review",
    label: "Review",
    short: "Confirm changes",
    contextTitle: "No surprise mutations",
    contextBody: "This is the boundary before Meridian changes the server.",
    contextItems: ["Target server", "Planned changes", "Explicit confirmation"],
  },
  {
    id: "complete",
    label: "Progress",
    short: "Evidence",
    contextTitle: "Evidence over optimism",
    contextBody: "Watch progress, results, and recovery steps in one place.",
    contextItems: ["Operation state", "Event timeline", "Result or recovery hint"],
  },
];

export const studioHelp = {
  serverTitle: "A friendly name you recognize later, such as Family VPN or London VPS.",
  serverPublicIp: "Use the public IPv4 or IPv6 address from your VPS provider. Do not use an internal/private IP.",
  sshUsername: "This is the Linux login user. Many providers use root, ubuntu, debian, or admin.",
  sshPort: "Use 22 unless your provider explicitly gave you another SSH port.",
  sshCommandPaste:
    "Optional shortcut. Paste the SSH command your provider shows, and Studio will fill username, IP, port, and title.",
  sshPassword:
    "Used only for this key setup request. Studio never saves it in JSON, localStorage, logs, URLs, or commands.",
  disablePasswordAuth:
    "Advanced safety setting for deploy hardening. Key setup verifies access first; deploy performs SSH hardening later.",
  savedServer: "Choose a server you already saved and validated. Raw IP and user fields stay behind the scenes.",
  displayName: "Shown on generated connection pages for clients.",
  clientName: "The first connection profile Meridian creates for you.",
  hardening: "Recommended for a new VPS. It may be too strict if the server already hosts other services.",
  geoBlock: "Blocks RU destinations on this server. Use route policy later when you want RU traffic to exit through a regional server instead.",
  sni: "A common website name used by Reality during network probes.",
  domain: "Optional. Use it when you already own a domain pointed at this server.",
  color: "Only affects the connection page appearance.",
  icon: "Optional emoji or image URL shown on connection pages.",
  pq: "Experimental. Keep off unless you specifically want to test it.",
  warp: "Routes outbound server traffic through WARP. Useful for some networks, unnecessary for most first installs.",
  pastedOutput: "Optional advanced tool. Paste one JSON envelope or JSONL events from the CLI to inspect them.",
};

export const studioFaqs = {
  welcome: [
    ["What is a VPS?", "A small rented Linux server. Meridian connects to it by SSH and installs VPN services there."],
    ["What is a public IP?", "The internet-facing address for the server. Examples use addresses like 198.51.100.10."],
    ["What if I only have a password?", "That is enough for Local Studio. It can use the password once to install a safer SSH key."],
  ],
  connect: [
    ["What does SSH validation mean?", "Studio tries a normal SSH connection and a harmless sudo check. It does not deploy anything."],
    ["What if it says permission denied?", "The server probably needs key setup, or the username/password differs from the provider page."],
    ["What if the port is closed?", "Check the provider firewall and confirm the SSH port. Most servers use port 22."],
  ],
  keys: [
    ["What is the private key?", "The secret half of SSH login. It stays on your computer and must not be pasted into websites."],
    ["What is the public key?", "The safe half that goes on the server. It lets the server recognize your private key later."],
  ],
};

export function requiredHelpKeys() {
  return Object.keys(studioHelp);
}
