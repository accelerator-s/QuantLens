if (location.pathname === '/' && !location.hash) {
  history.replaceState(history.state, '', `${location.pathname}${location.search}#dashboard`);
}
