import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { markdownResponse } from '../lib/docs-markdown';

// The guide `meridian help agents` prints; the CLI package owns it.
export const GET: APIRoute = () =>
  markdownResponse(fs.readFileSync(path.resolve('../src/meridian/data/agents.md'), 'utf-8'));
