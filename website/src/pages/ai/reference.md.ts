import type { APIRoute } from 'astro';
import { aiReference } from '../../../scripts/ai-docs.mjs';
import { markdownResponse } from '../../lib/docs-markdown';

// The same bundle `make ai-docs` puts into the package for `meridian doctor --ai`.
export const GET: APIRoute = () => markdownResponse(aiReference());
