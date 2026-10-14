import type { APIRoute } from 'astro';
import { llmsFull } from '../../scripts/ai-docs.mjs';
import { markdownResponse } from '../lib/docs-markdown';

export const GET: APIRoute = () => markdownResponse(llmsFull());
