// Sort the complete collection before pagination; equal or missing dates stay stable.
export function newestFirst(rows, keys = ['created_at', 'updated_at']) {
  function timestamp(row) {
    for (const key of keys) {
      const value = key.split('.').reduce((item, part) => item?.[part], row);
      if (value == null || value === '') continue;
      const time = value instanceof Date ? value.getTime() : Date.parse(value);
      if (Number.isFinite(time)) return time;
    }
    return -Infinity;
  }
  return rows.map((row, index) => ({row, index, time: timestamp(row)}))
    .sort((a, b) => a.time === b.time ? a.index - b.index : a.time > b.time ? -1 : 1)
    .map(item => item.row);
}
