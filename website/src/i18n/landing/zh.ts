import type { LandingStrings } from './en';

export const zh: LandingStrings = {
  // Page metadata
  'page.title': 'Meridian — 抗审查代理，一键正确部署',
  'page.description':
    '一条命令即可部署加固后的 VLESS+Reality 代理服务器，并生成可分享的链接。开源。',
  'page.ogAlt': 'Meridian：与你关心的人分享安全的互联网',

  // Hero
  'hero.kicker': '开源互联网自由工具',
  'hero.title': '与你关心的人分享<em>安全的互联网</em>',
  'hero.sub':
    '一条命令即可搭建一台难以与普通 HTTPS 区分的代理服务器，防火墙、TLS 和路由都已为你配置好。可通过链接或二维码分享访问权限。',
  'hero.cta': '开始使用',
  'hero.docs': '阅读文档',
  'hero.previewAlt': '连接页面——你的朋友收到的内容',
  'hero.caption': '你的朋友收到的页面——<a href="/demo/">查看演示</a>',

  // How it works
  'setup.label': '工作原理',
  'setup.title': '两条命令，得到可用的代理',
  'setup.desc':
    '你需要一台 Debian 或 Ubuntu 的 VPS（每月 5 美元的就够用）和电脑上的终端。其余由 Meridian 完成。',
  'setup.step1.label': '第 1 步——安装',
  'setup.step2.label': '第 2 步——设置',
  'setup.then': '然后',
  'setup.hint':
    '向导会问几个简单的问题，展示一份摘要供你确认，然后设置你的服务器。若只有一台服务器且不想回答问题，运行 <code>meridian deploy IP</code> 即可，详见 <a href="{base}/cli-reference/">CLI 参考</a>。',
  'setup.terminalAlt': '终端中的 Meridian 部署输出',
  'setup.video':
    '用 <code>meridian setup</code> 完成设置的全过程：3.5 分钟，英文讲解并附字幕。<a href="/video/meridian-setup.mp4" download>下载视频</a>',
  'setup.videoLabel': '视频：用 Meridian 搭建个人 VPN',
  'setup.details': '幕后发生了什么',
  'setup.idempotent':
    '可放心重复运行。再次运行会保留凭证和密钥，并就地继续或更新部署。',

  // Behind-the-scenes timeline (title, then the text that follows it)
  'step.1.title': '安装 Docker',
  'step.1.text': '，并部署由 <strong>Remnawave</strong> 面板管理的 <strong>Xray</strong>',
  'step.2.title': '生成 x25519 密钥对',
  'step.2.text': '——为 Reality 认证生成唯一的加密密钥',
  'step.3.title': '加固服务器',
  'step.3.text':
    '——UFW 只开放你的 SSH 端口、443（TCP 和 UDP）以及用于证书的 80；SSH 仅允许密钥登录，启用 fail2ban 和 <strong>BBR</strong> 拥塞控制',
  'step.4.title': '配置 VLESS+Reality',
  'step.4.text': '，监听 TCP 443，探测者看到的是真实网站的证书',
  'step.5.title': '添加 XHTTP 和 Hysteria2',
  'step.5.text': '——XHTTP 经由 nginx 使用同一端口，Hysteria2 使用 UDP 443 并启用 salamander 混淆',
  'step.6.title': '输出二维码',
  'step.6.text': '和连接页面，在真实流量通过新订阅之后生成',

  // Command builder section
  'builder.label': '命令生成器',
  'builder.title': '生成你的命令',
  'builder.desc':
    '选择选项并复制完整命令。涵盖部署、中继、检查、伪装站点扫描、诊断和卸载。',

  // Command builder widget
  'builder.tab.deploy': '部署',
  'builder.tab.relay': '中继',
  'builder.tab.check': '检查',
  'builder.tab.scan': 'SNI 扫描',
  'builder.tab.doctor': '诊断',
  'builder.tab.teardown': '卸载',
  'builder.desc.deploy':
    '在一台服务器上部署 Reality、XHTTP 和 Hysteria2。会配置 Docker、Remnawave 面板、nginx、防火墙和 TLS。',
  'builder.desc.relay':
    '在境内服务器上添加 TCP 中继，转发到境外的出口服务器。必须先部署出口服务器。',
  'builder.desc.check':
    '检查新服务器能否运行 Meridian（SNI、端口、DNS、系统、磁盘），或检查已部署的服务器是否健康。',
  'builder.desc.scan':
    '用 RealiTLScanner 在你服务器所在的网络中寻找伪装（SNI）站点，然后选择其一。',
  'builder.desc.doctor': '收集用于提交问题报告的诊断信息。敏感信息会被脱敏。',
  'builder.desc.teardown':
    '从服务器上移除 Meridian。单服务器部署会被完整移除，包括本地配置。',
  'builder.field.server': '服务器',
  'builder.field.server.placeholder': '198.51.100.10 或 local',
  'builder.field.server.hint':
    '服务器 IP、已保存的服务器名称，或填 "local" 直接在服务器本机上运行。留空则会提示你输入。',
  'builder.field.relayServer': '中继服务器',
  'builder.field.relayServer.hint': '负责转发流量的境内服务器的 IP。',
  'builder.field.user': 'SSH 用户',
  'builder.field.user.hint': '默认：已保存的用户，否则为 root。非 root 用户需要免密码 sudo。',
  'builder.field.sshPort': 'SSH 端口',
  'builder.field.sshPort.hint': '默认：已保存的端口，否则为 22。',
  'builder.field.exit': '出口服务器',
  'builder.field.exit.placeholder': '198.51.100.10 或服务器名称',
  'builder.field.exit.hint': '此中继所转发到的已部署出口服务器的 IP 或名称。',
  'builder.field.domain': '域名',
  'builder.field.domain.hint':
    '可选。将 XHTTP 移到你的域名上并添加 WSS；两者都可以经由 Cloudflare CDN。',
  'builder.field.sni': '伪装站点（SNI）',
  'builder.field.sni.placeholder': '自动',
  'builder.field.sni.hint':
    'Reality 所模仿的网站。保持自动，deploy 会选择一个能在你的服务器上使用的站点；也可以用 SNI 扫描标签页来寻找。',
  'builder.field.relaySni.hint': '保持自动即可沿用出口服务器的伪装站点。',
  'builder.field.clientName': '首个客户端',
  'builder.field.clientName.hint': '第一个连接者的名称。默认："default"。',
  'builder.field.displayName': '页面标题',
  'builder.field.displayName.placeholder': 'Alice 的 VPN',
  'builder.field.displayName.hint': '显示在连接页面上的标签。',
  'builder.field.icon': '页面图标',
  'builder.field.icon.hint': '连接页面使用的表情符号或图片 URL。',
  'builder.field.color': '页面配色',
  'builder.field.color.hint': '连接页面的颜色主题。',
  'builder.field.color.default': '默认（海洋）',
  'builder.palette.ocean': '海洋',
  'builder.palette.sunset': '日落',
  'builder.palette.forest': '森林',
  'builder.palette.lavender': '薰衣草',
  'builder.palette.rose': '玫瑰',
  'builder.palette.slate': '石板',
  'builder.field.relayName': '中继名称',
  'builder.field.relayName.hint': '可选的易记名称，例如 "ru-moscow"。',
  'builder.field.port': '监听端口',
  'builder.field.port.hint': '中继监听的端口。默认：443。',
  'builder.field.harden': '不改动 SSH 和防火墙',
  'builder.field.harden.hint': '仅在此服务器上还有其他服务时使用；加固默认开启。',
  'builder.field.ai': '适合 AI 的输出',
  'builder.field.ai.hint': '将供 ChatGPT 或 Claude 使用的提示词复制到剪贴板。',
  'builder.field.yes': '跳过确认',
  'builder.field.yes.hint': '用于脚本和非交互式 SSH 会话。',
  'builder.command': '命令',
  'builder.copy': '复制',
  'builder.copied': '已复制',
  'builder.wizard':
    '<strong>第一次使用？</strong>直接运行 <code>meridian setup</code>，向导会询问它需要的一切。',
  'builder.status.invalid': '请填写 IP 地址、已保存的服务器名称，或 "local"。',
  'builder.status.local': '直接在服务器本机上运行，无需 SSH，以 root 身份或使用免密码 sudo。',
  'builder.status.domainMissing': '请填写域名，或取消勾选。',
  'builder.status.deploy': '部署 Reality、XHTTP 和 Hysteria2。',
  'builder.status.deployDomain': '部署 Reality、XHTTP 和 Hysteria2，并在你的域名上添加 WSS。',
  'builder.status.relayNeed': '请填写中继服务器及其转发到的出口服务器。',
  'builder.status.relayOk': '添加经由中继进入的 Reality 条目；其他方式仍直接连接出口服务器。',
  'builder.status.checkNeed': '不指定服务器时，会检查已保存的整个集群。',
  'builder.status.checkOk': '只运行检查，不做任何更改。',
  'builder.status.scanNeed': '请填写你的服务器，以寻找它附近的伪装站点。',
  'builder.status.scanOk': '扫描服务器所在的网络，让你选择站点，并打印部署命令。',
  'builder.status.doctorNeed': '不指定服务器时，报告已保存的那台。',
  'builder.status.doctorOk': '收集脱敏后的报告。开启适合 AI 的输出可得到现成的提示词。',
  'builder.status.doctorAi': '收集脱敏后的报告，并将适合 AI 的提示词复制到剪贴板。',
  'builder.status.teardownNeed': '不指定服务器时，若只保存了一台则使用那台。',
  'builder.status.teardownOk': '在多服务器集群中，请先用 meridian node remove 移除该服务器的角色。',

  // How it avoids detection
  'tech.label': '技术',
  'tech.title': '它如何避免被检测',
  'tech.desc':
    'OpenVPN 和 WireGuard 的流量有可识别的特征。VLESS+Reality 连接与访问真实网站的普通 HTTPS 很难区分。',
  'tech.dpi': '深度包检测（DPI）',
  'tech.dpi.body':
    '<p>DPI 通过分析流量模式来识别代理协议。OpenVPN 和 WireGuard 的握手很容易被识别和封锁。</p><p><strong>VLESS+Reality</strong> 运行在 443 端口上的标准 TLS 1.3 连接之内，因此 DPI 看到的是指向伪装站点的普通 HTTPS。每台服务器还提供 XHTTP 和 Hysteria2，所以一种方式被封锁时，其他方式仍可使用。</p>',
  'tech.probe': '主动探测',
  'tech.probe.body':
    '<p>审查者会主动连接可疑的服务器并尝试识别其指纹。如果服务器的响应与它声称的网站不一致，就会被封锁。</p><p><strong>Reality</strong> 借用真实网站的 TLS 身份：默认是托管在你服务器附近的网站，或 <code>www.google.com</code>。没有你的密钥的探测会被转发到该网站，看到的是它真实的证书。只有持有你的密钥的客户端才能获得代理隧道。</p>',
  'tech.fp': 'TLS 指纹识别',
  'tech.fp.body':
    '<p>每个 TLS 客户端都会发送独特的“Client Hello”指纹。如果指纹与其声称的应用不符，审查者就会将该连接标记出来。</p><p>客户端应用使用 <strong>uTLS</strong>，发送与 Chrome 相同的 Client Hello。Meridian 还会为每个 Reality 和 XHTTP 条目发布一份 Firefox 指纹的副本，因此一种指纹被标记时，另一种仍可使用。</p>',

  // Connect
  'connect.label': '连接',
  'connect.title': '扫码即连',
  'connect.desc':
    '部署完成后，你会得到一个带二维码的连接页面。把它发给需要的人，点一下即可连接。',
  'connect.apps.more':
    '连接页面会优先列出这些应用，并为每个平台提供其他应用的链接。<a href="/demo/">查看演示</a>。',
  'connect.clock':
    '请在每台设备上开启自动设置日期和时间。设备时钟不准时，连接会失败。',

  // Architecture
  'arch.label': '架构',
  'arch.title': '内部结构',
  'arch.alt':
    'Meridian 架构：nginx 按 SNI 将 TCP 443 路由到 Xray Reality、XHTTP 和连接页面；Hysteria2 监听 UDP 443',
  'arch.standalone': '独立模式（无域名）',
  'arch.standalone.body':
    '<p><strong>nginx</strong> 占用 TCP 443 端口，并通过其 stream 模块按 TLS SNI 路由连接。Reality 连接交给 <strong>Xray</strong>。其余连接进入 nginx 的 http 模块，由它通过 HTTPS 提供连接页面、订阅和 XHTTP，使用的是为服务器 IP 签发的短期 Let\'s Encrypt 证书（由 acme.sh 签发）。<strong>Hysteria2</strong> 监听 UDP 443，并启用 salamander 混淆。</p><p>Remnawave 管理面板由 nginx 在一条秘密路径上反向代理，可直接通过 HTTPS 访问，无需 SSH 隧道。</p>',
  'arch.domain': '域名模式（CDN 备用）',
  'arch.domain.body':
    '<p>使用 <code>--domain</code> 时，nginx 会为你的域名获取 Let\'s Encrypt 证书，并将该 SNI 路由到域名对应的路径。XHTTP 移到域名上，并新增 <strong>VLESS+WSS</strong>。如果由 Cloudflare 代理该域名，两者都经由 CDN 传输，即使服务器 IP 被封锁也能继续使用。面板和连接页面仍使用 IP 证书。</p>',
  'arch.relay': '中继模式（境内入口）',
  'arch.relay.body':
    '<p>境内服务器上的 <strong>Realm</strong> TCP 转发器会把连接转交给你位于境外的出口服务器。中继只承载 Reality；XHTTP、WSS 和 Hysteria2 仍直接连接出口服务器。加密在应用与出口服务器之间端到端进行，因此中继看不到明文。</p><p>用 <code>meridian relay add RELAY_IP --exit EXIT</code> 添加中继。订阅和连接页面会自动包含中继条目。</p>',

  // Closing call to action
  'cta.title': '准备好开始了吗？',
  'cta.desc':
    '安装 Meridian，部署到你的服务器，并与需要的人分享访问权限。一次部署通常需要 3 到 10 分钟。',
  'cta.install': '安装 Meridian',
};
