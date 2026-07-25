export function formatDateTime(iso) {
  if (!iso) return iso
  return iso.replace('T', ' ').slice(0, 19)
}
