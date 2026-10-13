import { defineRouteMiddleware } from '@astrojs/starlight/route-data';

// The logo leads back to the landing page in the reader's language, and every page
// announces its Markdown twin for agents.
export const onRequest = defineRouteMiddleware((context) => {
  const route = context.locals.starlightRoute;
  const locale = route.locale ?? 'en';
  route.siteTitleHref = locale === 'en' ? '/' : `/${locale}/`;
  route.head.push({ tag: 'link', attrs: { rel: 'alternate', type: 'text/markdown', href: `/${route.id}.md` } });
});
