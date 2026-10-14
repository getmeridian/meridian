import { defineRouteMiddleware } from '@astrojs/starlight/route-data';

// The logo leads back to the landing page in the reader's language, every page announces
// its Markdown twin for agents, and link previews use the locale's card (figures/og.html).
export const onRequest = defineRouteMiddleware((context) => {
  const route = context.locals.starlightRoute;
  const locale = route.locale ?? 'en';
  route.siteTitleHref = locale === 'en' ? '/' : `/${locale}/`;
  route.head.push({ tag: 'link', attrs: { rel: 'alternate', type: 'text/markdown', href: `/${route.id}.md` } });
  const card = new URL(locale === 'en' ? '/img/og.jpg' : `/img/og-${locale}.jpg`, context.site).href;
  route.head.push(
    { tag: 'meta', attrs: { property: 'og:image', content: card } },
    { tag: 'meta', attrs: { property: 'og:image:type', content: 'image/jpeg' } },
    { tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
    { tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
    { tag: 'meta', attrs: { name: 'twitter:image', content: card } },
  );
});
