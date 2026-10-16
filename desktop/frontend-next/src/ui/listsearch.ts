export function listMatcher(query: string) {
  const words = query.normalize("NFC").toLowerCase().trim().split(/\s+/).filter(Boolean);
  return (values: readonly (string | undefined)[]) => words.every((word) =>
    values.some((value) => value?.normalize("NFC").toLowerCase().includes(word)),
  );
}
