// Every word on screen, per narration language. Pure data: the composition and
// scripts/export.mjs (chapter titles) both import it, so keep it free of imports.

export type Lang = "en" | "ru";

type Step = { step: string; title: string; hint: string };

const en = {
  intro: {
    kicker: "Meridian · free and open source",
    line1: "Your own VPN,",
    before: "in about ",
    accent: "ten minutes",
    after: ".",
    tagline: "Deploy it right. Share it easily.",
  },
  needs: {
    kicker: "What you need",
    title: "Two things.",
    server: "A small rented server",
    serverLines: [
      "A VPS with Debian or Ubuntu",
      "The cheapest plan is plenty",
      "In the country you want to browse from",
    ],
    computer: "A computer with a terminal",
    computerLines: ["Where you can log in to the server over SSH", "That's where Meridian runs"],
  },
  install: {
    kicker: "Install",
    title: "One line on your computer.",
    window: "Terminal",
    or: "# or, if you already use uv:",
  },
  steps: {
    server: {
      step: "Step 1 · Servers",
      title: "Run meridian setup",
      hint: "Arrow keys and Enter. ? explains a step, Esc goes back.",
    },
    roles: {
      step: "Step 2 · Roles",
      title: "Defaults do the work",
      hint: "One server runs the panel and carries the traffic.",
    },
    camouflage: {
      step: "Step 3 · Camouflage",
      title: "Pick a disguise",
      hint: "Real sites in your server's own network come first.",
    },
    users: { step: "Step 4 · People", title: "Name your people", hint: "Any language. Everyone gets their own link." },
    apply: {
      step: "Step 5 · Set up",
      title: "Meridian does the rest",
      hint: "Hardening, firewall, certificates, panel, VPN. Then it checks every link.",
    },
    handoff: {
      step: "Done",
      title: "Share the links",
      hint: "A connection page and a subscription link for each person.",
    },
  } as Record<string, Step>,
  phone: {
    step: "On her phone",
    title: "One tap to connect",
    hint: "A QR code, an app to install, and one button that adds every server. The app keeps it up to date.",
  },
  later: {
    kicker: "Later on",
    title: "Everyday commands.",
    commands: [
      ["meridian client add Grandma", "a new person, a new link"],
      ["meridian client remove Alex", "their link stops working"],
      ["meridian check", "looks over your server"],
      ["meridian test", "real traffic through every connection"],
    ],
  },
  why: {
    kicker: "Why it's hard to block",
    title: "It looks like an ordinary website.",
    body: "Every server offers three methods. Apps switch on their own if one gets blocked.",
    reality: "a visit to the camouflage site",
    xhttp: "ordinary HTTPS to your server",
    hysteria: "disguised UDP, fast on poor networks",
    blocked: "blocked: the app moves on",
    connected: "connected",
  },
  outro: { footer: "Free and open source · Narration voice: elevenlabs.io" },
  thumbnail: { kicker: "Meridian · full walkthrough", after: ", start to finish" },
  chapters: {
    intro: "What Meridian does",
    needs: "What you need",
    install: "Install",
    server: "meridian setup: the server",
    roles: "Defaults and the VPN's name",
    camouflage: "Camouflage",
    users: "People and review",
    apply: "Setting up",
    handoff: "Links to share",
    phone: "On the phone",
    later: "Everyday commands",
    why: "Why it's hard to block",
  } as Record<string, string>,
};

export type Copy = typeof en;

const ru: Copy = {
  intro: {
    kicker: "Meridian · бесплатно и с открытым кодом",
    line1: "Свой VPN",
    before: "примерно за ",
    accent: "десять минут",
    after: ".",
    tagline: "Настроить как надо. Поделиться легко.",
  },
  needs: {
    kicker: "Что понадобится",
    title: "Две вещи.",
    server: "Небольшой арендованный сервер",
    serverLines: [
      "VPS с Debian или Ubuntu",
      "Хватит самого дешёвого тарифа",
      "В стране, через которую хотите выходить в интернет",
    ],
    computer: "Компьютер с терминалом",
    computerLines: ["С него вы заходите на сервер по SSH", "Здесь и работает Meridian"],
  },
  install: {
    kicker: "Установка",
    title: "Одна команда на вашем компьютере.",
    window: "Терминал",
    or: "# или, если у вас уже есть uv:",
  },
  steps: {
    server: {
      step: "Шаг 1 · Серверы",
      title: "Запустите meridian setup",
      hint: "Стрелки и Enter. ? объясняет шаг, Esc — назад.",
    },
    roles: { step: "Шаг 2 · Роли", title: "Всё по умолчанию", hint: "Один сервер держит панель и пропускает трафик." },
    camouflage: {
      step: "Шаг 3 · Маскировка",
      title: "Выберите маскировку",
      hint: "Сначала идут настоящие сайты из сети вашего сервера.",
    },
    users: { step: "Шаг 4 · Люди", title: "Кто будет подключаться", hint: "На любом языке. У каждого своя ссылка." },
    apply: {
      step: "Шаг 5 · Установка",
      title: "Остальное сделает Meridian",
      hint: "Защита, файрвол, сертификаты, панель, VPN. А в конце проверка каждой ссылки.",
    },
    handoff: {
      step: "Готово",
      title: "Отправьте ссылки",
      hint: "Страница подключения и ссылка на подписку для каждого.",
    },
  },
  phone: {
    step: "На её телефоне",
    title: "Одно нажатие",
    hint: "QR-код, приложение для установки и одна кнопка, которая добавляет все серверы. Приложение само следит за обновлениями.",
  },
  later: {
    kicker: "Потом",
    title: "Команды на каждый день.",
    commands: [
      ["meridian client add Бабушка", "новый человек, новая ссылка"],
      ["meridian client remove Саша", "его ссылка перестаёт работать"],
      ["meridian check", "проверяет сервер"],
      ["meridian test", "настоящий трафик через каждое подключение"],
    ],
  },
  why: {
    kicker: "Почему это сложно заблокировать",
    title: "Это выглядит как обычный сайт.",
    body: "Каждый сервер предлагает три способа. Если один заблокируют, приложения переключатся сами.",
    reality: "заход на сайт-маскировку",
    xhttp: "обычный HTTPS к вашему серверу",
    hysteria: "замаскированный UDP, быстрый на плохих сетях",
    blocked: "заблокирован: приложение переключается",
    connected: "подключено",
  },
  outro: { footer: "Бесплатно и с открытым кодом · Озвучка: Qwen3-TTS" },
  thumbnail: { kicker: "Meridian · полный разбор", after: " от начала до конца" },
  chapters: {
    intro: "Что делает Meridian",
    needs: "Что понадобится",
    install: "Установка",
    server: "meridian setup: сервер",
    roles: "Настройки по умолчанию и название VPN",
    camouflage: "Маскировка",
    users: "Люди и проверка плана",
    apply: "Установка на сервер",
    handoff: "Ссылки для отправки",
    phone: "На телефоне",
    later: "Команды на каждый день",
    why: "Почему это сложно заблокировать",
  },
};

export const COPY: Record<Lang, Copy> = { en, ru };
