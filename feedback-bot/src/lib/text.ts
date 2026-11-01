export const russian = (language?: string) => language?.toLowerCase().startsWith('ru') ?? false;

export function text(language: string | undefined, key: keyof typeof en): string {
  return (russian(language) ? ru : en)[key];
}

const en = {
  welcome: 'Send Meridian feedback here: text, screenshots, documents or voice messages. A project maintainer will receive your message and may reply through this bot. Do not send passwords, subscription links or connection credentials. Bot database records are kept for 90 days; copies in the maintainer’s Telegram inbox may remain. /privacy explains storage; /delete removes your bot database records.',
  privacy: 'This bot stores your Telegram chat ID, message references, feedback text/caption and delivery status for 90 days, and copies your feedback to the maintainer’s private Telegram inbox. Telegram bot chats are not end-to-end encrypted. /delete removes stored feedback and reply routes, not messages already delivered to Telegram. Daily maintenance and bot interactions delete expired records; expired routes are not usable after cleanup.',
  deleted: 'Your feedback and reply-routing records have been deleted from the bot database. Messages already delivered to Telegram were not deleted.',
  received: 'Thanks—your feedback has been received.',
  limited: 'Please wait a minute before sending more feedback (up to 5 messages per minute).',
  unsupported: 'Please send text, a screenshot, document, audio, video or voice message.',
  unavailable: 'The original message is unavailable or cannot be copied. Please resend it as ordinary text or an attachment.',
};

const ru: typeof en = {
  welcome: 'Пришлите отзыв о Meridian: текст, скриншот, документ или голосовое сообщение. Разработчик получит сообщение и сможет ответить через бота. Не отправляйте пароли, ссылки подписок или данные подключения. Записи в базе бота хранятся 90 дней; копии у разработчика в Telegram могут сохраниться. /privacy — о хранении данных; /delete — удалить ваши записи из базы бота.',
  privacy: 'Бот хранит ID вашего чата в Telegram, ссылки на сообщения, текст/подпись отзыва и статус доставки 90 дней и копирует отзыв в личный чат разработчика. Чаты с ботами не имеют сквозного шифрования. /delete удаляет записи и маршруты ответов из базы, но не доставленные сообщения в Telegram. Ежедневное обслуживание и обращения к боту удаляют просроченные записи; после очистки ответить по ним нельзя.',
  deleted: 'Ваши отзывы и маршруты ответов удалены из базы бота. Уже доставленные сообщения в Telegram не удалены.',
  received: 'Спасибо! Ваш отзыв получен.',
  limited: 'Подождите минуту перед отправкой новых отзывов (не более 5 сообщений в минуту).',
  unsupported: 'Пришлите текст, скриншот, документ, аудио, видео или голосовое сообщение.',
  unavailable: 'Исходное сообщение недоступно или его нельзя скопировать. Пришлите его ещё раз обычным текстом или вложением.',
};
