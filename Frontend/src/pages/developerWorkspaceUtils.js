export const MAX_PROMPT_CHARS = 32768;
export const MAX_DISPLAY_CHARS = 65536;
export const DEFAULT_LIMITS = {
  max_messages: 60,
  max_input_tokens: 8192,
  max_output_tokens: 1024,
};

export function workspaceLimits(pricing, pair) {
  return Object.fromEntries(
    Object.entries(DEFAULT_LIMITS).map(([key, cap]) => {
      const value = Number(pricing?.playground_limits?.[key]);
      if (key === "max_output_tokens" && Number(pair?.max_output_tokens) > 0)
        return [
          key,
          Math.min(
            cap,
            Number(pair.max_output_tokens),
            value > 0 ? value : cap,
          ),
        ];
      return [
        key,
        Number.isFinite(value) && value > 0
          ? Math.min(Math.floor(value), cap)
          : cap,
      ];
    }),
  );
}
export function estimateTokens(messages) {
  // Deliberately an estimate, not a claim about the model's tokenizer.
  return messages.reduce(
    (sum, message) =>
      sum + Math.ceil(new TextEncoder().encode(message.content).length / 4) + 6,
    0,
  );
}
export function validMessages(messages) {
  return (
    Array.isArray(messages) &&
    messages.every(
      (m) =>
        ["user", "assistant"].includes(m?.role) &&
        typeof m.content === "string" &&
        m.content.length <= MAX_PROMPT_CHARS,
    )
  );
}
export function messageLimitError(messages, limits) {
  if (
    !validMessages(messages) ||
    messages.length > limits.max_messages ||
    messages.reduce((n, m) => n + m.content.length, 0) > MAX_PROMPT_CHARS ||
    new TextEncoder().encode(JSON.stringify(messages)).length >
      MAX_PROMPT_CHARS ||
    estimateTokens(messages) > limits.max_input_tokens
  )
    return "historyTooLong";
  return "";
}
export function boundedDisplay(messages) {
  const result = messages.slice(-60);
  let count = result.reduce((n, m) => n + m.content.length, 0);
  while (count > MAX_DISPLAY_CHARS && result.length > 1)
    count -= result.shift().content.length;
  return result;
}
export function okData(response) {
  if (
    response?.status !== "ok" ||
    !response.data ||
    typeof response.data !== "object"
  )
    throw new Error("invalid_response");
  return response.data;
}
export function errorText(error, fallback) {
  if (error?.code === "refund_pending") return "refundPending";
  if (error?.terminal === true && error?.refund_confirmed === true)
    return "refundedFailed";
  if (["budget_exceeded", "insufficient_sparks"].includes(error?.code))
    return "insufficient";
  if (error?.code === "timeout") return "timeout";
  if (error?.status === 402) return "insufficient";
  if ([401, 403].includes(error?.status)) return "unauthorized";
  if (error?.status === 429) return "rateLimited";
  if (error?.code === "invalid_response") return "invalidResponse";
  return fallback;
}
export function connectionCode(format, baseUrl, pair, prompt = "Hello!") {
  if (!baseUrl || !pair) return "";
  const maxTokens = workspaceLimits(null, pair).max_output_tokens;
  if (format === "python")
    return `import os\nfrom openai import OpenAI\n\nclient = OpenAI(\n    api_key=os.environ["UWENZAI_KEY"],\n    base_url=${JSON.stringify(baseUrl)},\n)\n\nresponse = client.chat.completions.create(\n    model=${JSON.stringify(pair.model)},\n    extra_body={"provider": ${JSON.stringify(pair.provider)}},\n    messages=[{"role": "user", "content": ${JSON.stringify(prompt)}}],\n    max_tokens=${maxTokens},\n)\nprint(response.choices[0].message.content)`;
  // A quoted heredoc safely handles model/provider strings, including apostrophes.
  return `curl ${JSON.stringify(`${baseUrl.replace(/\/+$/, "")}/chat/completions/`)} \\\n  -H "Authorization: Bearer $UWENZAI_KEY" \\\n  -H "Content-Type: application/json" \\\n  --data-binary @- <<'UWENZAI_REQUEST'\n${JSON.stringify({ model: pair.model, provider: pair.provider, messages: [{ role: "user", content: prompt }], max_tokens: maxTokens }, null, 2)}\nUWENZAI_REQUEST`;
}
