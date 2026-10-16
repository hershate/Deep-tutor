import { tokenizeJson } from "./jsonTokens";

/** JSON text with keys, strings, numbers and literals coloured by the same
 *  syntax tokens a fenced code block uses. The text is unchanged: selecting and
 *  copying it yields exactly what the model sent. */
export function JsonText({ text }: { text: string }) {
  return (
    <>
      {tokenizeJson(text).map((tok, i) => (tok.kind === "plain" ? tok.text : <span key={i} className={`jt-${tok.kind}`}>{tok.text}</span>))}
    </>
  );
}
