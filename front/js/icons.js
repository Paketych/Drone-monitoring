const paths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  house:
    '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21v-8h6v8"/>',
  tasks:
    '<rect x="5" y="4" width="15" height="17" rx="2"/><path d="M9 4V2m7 2V2M9 10l2 2 4-4m-6 9h7"/>',
  drone:
    '<rect x="9" y="9" width="6" height="6" rx="1.5"/><path d="m7 7 3 3m4 4 3 3M7 17l3-3m4-4 3-3"/><circle cx="5" cy="5" r="4"/><circle cx="19" cy="5" r="4"/><circle cx="5" cy="19" r="4"/><circle cx="19" cy="19" r="4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  upload:
    '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  back: '<path d="m14 5-7 7 7 7"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5m0 3h.01"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  phone:
    '<path d="M8 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-4l-5-2-2 3a15 15 0 0 1-7-7l3-2z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="15" cy="17" r="3" fill="currentColor" stroke="none"/>',
  file: '<path d="M14 2H5a1 1 0 0 0-1 1v18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8zM14 2v6h6M8 13h8m-8 4h6"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
  location:
    '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  shield:
    '<path d="m12 2 8 3v7c0 5-8 10-8 10S4 17 4 12V5z"/><path d="m8 11 3 3 5-6"/>',
  trend: '<path d="M3 17 9 11l4 4 8-10m-6 0h6v6"/>',
  print: '<path d="M6 9V3h12v6M6 18H3v-8h18v8h-3M6 14h12v8H6z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh: '<path d="M20 7V2l-4 4A8 8 0 1 0 20 14M20 7h-5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3h.01"/>',
  bolt: '<path d="m13 2-9 12h7l-1 8 10-13h-7z"/>',
};
export function icon(name, cls = "") {
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
}
