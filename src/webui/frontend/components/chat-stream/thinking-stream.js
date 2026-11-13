// Incremental <think> splitting. Keep only an unfinished tag/delimiter between
// chunks; a long reply is never rescanned on every token. Code stays literal.
export function createThinkingStream(emit) {
  let pending = "", depth = 0, code = null, linePrefix = "";
  function feed(chunk, final = false) {
    pending += chunk;
    const output = { content: [], reasoning: [] };
    function take(text) {
      output[depth ? "reasoning" : "content"].push(text);
      const newline = text.lastIndexOf("\n");
      linePrefix = newline >= 0 ? text.slice(newline + 1) : linePrefix + text;
      // Only the first four columns matter for fenced code detection.
      if (linePrefix.length > 4) linePrefix = "xxxx";
    }
    let index = 0;
    while (index < pending.length) {
      const rest = pending.slice(index);
      if (rest[0] === "`" || rest[0] === "~") {
        const run = rest.match(/^(`+|~+)/)[0];
        if (index + run.length === pending.length && !final) break;
        const fenced = /^ {0,3}$/.test(linePrefix) && run.length >= 3;
        if (code) {
          if (run[0] === code.char && (code.fenced ? fenced && run.length >= code.length : run.length === code.length)) code = null;
        } else if (run[0] === "`" || fenced) {
          code = { char: run[0], length: run.length, fenced };
        }
        take(run); index += run.length; continue;
      }
      if (!code && rest[0] === "<") {
        const tag = rest.match(/^<(\/?)think\s*>/i);
        if (tag) {
          depth = tag[1] ? Math.max(0, depth - 1) : depth + 1;
          index += tag[0].length; continue;
        }
        const lower = rest.toLowerCase();
        if (!final && (["<think", "</think"].some(tag => tag.startsWith(lower)) || /^<\/?think\s*$/i.test(rest))) break;
      }
      take(pending[index++]);
    }
    pending = pending.slice(index);
    for (const channel of ["reasoning", "content"]) {
      if (output[channel].length) emit(channel, output[channel].join(""));
    }
  }
  return { feed, finish: () => feed("", true) };
}
