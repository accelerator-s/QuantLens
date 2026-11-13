import { Marked } from "../../resources/vendor/marked/marked.js";
import DOMPurify from "../../resources/vendor/dompurify/purify.js";
import katex from "../../resources/vendor/katex/katex.js";

const escapeHtml = text => text.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

// Treat code as literal text, including examples containing <think> tags.
function codeEnd(source, index) {
  const run = source.slice(index).match(/^(`+|~{3,})/)?.[0];
  if (!run) return index;
  const prefix = source.slice(source.lastIndexOf("\n", index - 1) + 1, index);
  const fenced = run.length >= 3 && /^ {0,3}$/.test(prefix);
  if (run[0] === "~" && !fenced) return index;
  if (fenced) {
    const pattern = new RegExp(`^ {0,3}${run[0]}{${run.length},}[ \\t]*$`, "gm");
    pattern.lastIndex = source.indexOf("\n", index + run.length);
    if (pattern.lastIndex < 0) return source.length;
    const close = pattern.exec(source);
    return close ? close.index + close[0].length : source.length;
  }
  let next = source.indexOf(run, index + run.length);
  while (next >= 0) {
    if (source[next - 1] !== "`" && source[next + run.length] !== "`") return next + run.length;
    next = source.indexOf(run, next + run.length);
  }
  return index;
}

export function splitThinking(source) {
  let answer = "", thinking = "", depth = 0;
  for (let index = 0; index < source.length;) {
    const end = codeEnd(source, index);
    if (end > index) {
      if (depth) thinking += source.slice(index, end);
      else answer += source.slice(index, end);
      index = end;
      continue;
    }
    const tag = source.slice(index).match(/^<(\/?)think\s*>/i);
    if (tag) {
      if (tag[1]) depth = Math.max(0, depth - 1);
      else {
        if (!depth && thinking) thinking += "\n\n";
        depth++;
      }
      index += tag[0].length;
    } else {
      if (depth) thinking += source[index];
      else answer += source[index];
      index++;
    }
  }
  return { answer: answer.trim(), thinking: thinking.trim(), incomplete: depth > 0 };
}

function mathToken(source) {
  const opening = ["$$", "\\[", "\\(", "$"].find(delimiter => source.startsWith(delimiter));
  if (!opening || (opening === "$" && /\s/.test(source[1] || " "))) return;
  const closing = ({ "$$": "$$", "\\[": "\\]", "\\(": "\\)", "$": "$" })[opening];
  let end = source.indexOf(closing, opening.length);
  while (end >= 0 && source[end - 1] === "\\") end = source.indexOf(closing, end + closing.length);
  if (end < 0) return;
  const text = source.slice(opening.length, end);
  if (opening === "$" && (!text || /\s$/.test(text) || text.includes("\n") || /\d/.test(source[end + 1] || ""))) return;
  return { type: "chatMath", raw: source.slice(0, end + closing.length), text, display: opening === "$$" || opening === "\\[" };
}

function strongToken(source) {
  // Model replies put quoted emphasis next to Chinese text. Paired ** should
  // still work there, without CommonMark's punctuation/word boundary restriction.
  if (!/^\*\*(?!\*)\S/.test(source)) return;
  for (let index = 2; index < source.length;) {
    const code = codeEnd(source, index);
    if (code > index) { index = code; continue; }
    const math = mathToken(source.slice(index));
    if (math) { index += math.raw.length; continue; }
    if (source[index] === "\\") { index += 2; continue; }
    if (source.startsWith("**", index)) {
      // Leave triple delimiters to the standard nested emphasis parser.
      if (source[index + 2] === "*" || /\s/.test(source[index - 1])) return;
      const text = source.slice(2, index);
      return {
        type: "strong", raw: source.slice(0, index + 2), text,
        tokens: this.lexer.inlineTokens(text),
      };
    }
    index++;
  }
}

export function renderMarkdown(host, source) {
  const formulas = [];
  const markdown = new Marked({
    gfm: true, breaks: true,
    renderer: { html: ({ text }) => escapeHtml(text) },
    extensions: [{
      name: "chatStrong", level: "inline",
      start: source => source.indexOf("**"),
      tokenizer: strongToken,
    }, {
      name: "chatMath", level: "inline",
      start: source => source.search(/\$|\\[([]/),
      tokenizer: mathToken,
      renderer: token => {
        const index = formulas.push(token) - 1;
        return `<span data-chat-math="${index}"></span>`;
      },
    }],
  });
  host.classList.add("chat-markdown");
  host.innerHTML = DOMPurify.sanitize(markdown.parse(source), {
    USE_PROFILES: { html: true }, FORBID_TAGS: ["style", "form"], FORBID_ATTR: ["style", "id"],
  });
  for (const slot of host.querySelectorAll("[data-chat-math]")) {
    const formula = formulas[Number(slot.dataset.chatMath)];
    if (!formula) continue;
    katex.render(formula.text, slot, {
      displayMode: formula.display, throwOnError: false, trust: false,
      strict: "ignore", maxExpand: 1000, maxSize: 10,
    });
    slot.classList.toggle("chat-math--display", formula.display);
    slot.removeAttribute("data-chat-math");
  }
  for (const link of host.querySelectorAll("a[href]")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  for (const table of host.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "chat-markdown__table";
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
}

export function renderAssistant(host, source, reasoning = "") {
  const { answer, thinking, incomplete } = splitThinking(source);
  host.replaceChildren();
  const thoughts = [...new Set([reasoning.trim(), thinking].filter(Boolean))].join("\n\n");
  if (thoughts) {
    const details = document.createElement("details");
    details.className = "chat-thinking";
    details.open = !answer;
    const summary = document.createElement("summary");
    summary.textContent = incomplete ? "思考过程（未完整返回）" : "思考过程";
    const body = document.createElement("div");
    body.className = "chat-thinking__body";
    renderMarkdown(body, thoughts);
    details.append(summary, body);
    host.append(details);
  }
  if (answer) {
    const body = document.createElement("div");
    body.className = "chat__answer";
    renderMarkdown(body, answer);
    host.append(body);
  }
}
