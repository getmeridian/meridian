import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { getPosts } from '../../components/blog/posts';

export async function GET(context: APIContext) {
  const posts = await getPosts();
  return rss({
    title: 'Meridian Blog',
    description: 'How censorship circumvention works, why it matters, and how to stay connected.',
    site: context.site!,
    items: posts.map((post) => ({
      title: post.data.title,
      description: post.data.description,
      pubDate: post.data.date,
      link: `/blog/${post.id}/`,
      categories: [post.data.category, ...post.data.tags],
      author: post.data.author,
    })),
    customData: '<language>en</language>',
  });
}
