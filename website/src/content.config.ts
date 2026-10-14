import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { i18nLoader } from '@astrojs/starlight/loaders';
import { docsSchema, i18nSchema } from '@astrojs/starlight/schema';

const docs = defineCollection({
  // [a-z]* excludes the contributor notes (CLAUDE.md) from the docs
  loader: glob({ pattern: '**/[a-z]*.md', base: './src/content/docs' }),
  schema: docsSchema(),
});

const i18n = defineCollection({
  loader: i18nLoader(),
  schema: i18nSchema({
    extend: z.object({
      'meridian.nav.blog': z.string(),
      'meridian.nav.demo': z.string(),
      'meridian.page.copy': z.string(),
      'meridian.page.copied': z.string(),
      'meridian.page.view': z.string(),
      'meridian.page.open': z.string(),
      'meridian.page.prompt': z.string(),
    }),
  }),
});

const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    author: z.string(),
    category: z.string(),
    tags: z.array(z.string()),
    readingTime: z.number(),
  }),
});

export const collections = { docs, i18n, blog };
