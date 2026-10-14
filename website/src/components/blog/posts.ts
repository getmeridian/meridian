/** Shared blog helpers: the post list, category slugs, dates, and hero images. */
import type { ImageMetadata } from 'astro';
import { getCollection, type CollectionEntry } from 'astro:content';

export type Post = CollectionEntry<'blog'>;

/** Every post, newest first. */
export async function getPosts(): Promise<Post[]> {
  return (await getCollection('blog')).sort((a, b) => b.data.date.getTime() - a.data.date.getTime());
}

export function categorySlug(category: string): string {
  return category
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '');
}

export function categoryHref(category: string): string {
  return `/blog/category/${categorySlug(category)}/`;
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function formatDate(date: Date, month: 'short' | 'long' = 'short'): string {
  return date.toLocaleDateString('en-US', { year: 'numeric', month, day: 'numeric', timeZone: 'UTC' });
}

const heroes = import.meta.glob<{ default: ImageMetadata }>('../../content/blog/*/hero.webp', { eager: true });

/** The post's co-located hero.webp, if it has one. */
export function heroOf(post: Post): ImageMetadata | undefined {
  return heroes[`../../content/blog/${post.id}/hero.webp`]?.default;
}
