async function api(path, payload, signal = AbortSignal.timeout(15000)) {
  const response = await fetch(path, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": state.session || "",
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || "Please try again.");
  return body;
}
