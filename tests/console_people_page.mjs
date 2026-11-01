/* Minimal read-only page boundary for non-filter console scenarios. */
export function peoplePage(users) {
  const status = { '': users.length, active: 0, disabled: 0, limited: 0, expired: 0 };
  const tag = { '': users.length, none: 0 };
  const attention = { '': users.length, all: 0, expiring: 0, quota: 0, unused: 0, stopped: 0 };
  for (const user of users) {
    status[user.status] = (status[user.status] || 0) + 1;
    const key = user.tag || 'none'; tag[key] = (tag[key] || 0) + 1;
  }
  return { users, total: users.length, population: users.length, next_cursor: null,
    facets: { status, tag, attention, lifecycle: { '': users.length, unlimited: users.length }, never: 0, expires: users.length, quota: users.length } };
}

export function queryKey(raw) {
  const params = new URLSearchParams(raw);
  const query = { q: (params.get('q') || '').trim(), status: params.get('status') || '', tag: params.get('tag') || '',
    expires: params.get('expires') ? Number(params.get('expires')) : null,
    quota: params.get('quota') ? Number(params.get('quota')) : null, never: params.get('never') === '1',
    attention: params.get('attention') || '', lifecycle: params.get('lifecycle') || '', sort: params.get('sort') || 'name',
    order: params.get('order') || 'asc' };
  return JSON.stringify(Object.fromEntries(Object.entries(query).sort(([a], [b]) => a.localeCompare(b))));
}
