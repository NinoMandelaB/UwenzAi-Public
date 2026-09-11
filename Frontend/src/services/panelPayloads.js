const object = (value) =>
  value && typeof value === "object" && !Array.isArray(value);
const id = (value) => typeof value === "string" && value.length > 0;
const invalid = () => {
  throw new Error("Invalid response. Please reload and try again.");
};
const rows = (value, validate) => {
  if (value == null) return [];
  if (!Array.isArray(value)) return invalid();
  return value.map(validate);
};

export function validateReminder(row) {
  if (
    !object(row) ||
    !id(row.reminder_id) ||
    typeof row.reminder_text !== "string" ||
    typeof row.due_at !== "string" ||
    !Number.isFinite(Date.parse(row.due_at)) ||
    typeof row.due_at_local !== "string" ||
    !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(row.due_at_local) ||
    (row.character_name != null && typeof row.character_name !== "string") ||
    (row.timezone != null && typeof row.timezone !== "string")
  )
    return invalid();
  return row;
}
export const validateReminders = (data) => rows(data, validateReminder);

export function validateFact(row) {
  if (
    !object(row) ||
    !id(row.memory_fact_id) ||
    typeof row.fact_text !== "string"
  )
    return invalid();
  return row;
}
export function validateRelation(row) {
  if (
    !object(row) ||
    !id(row.memory_relation_id) ||
    !["subject", "predicate", "object"].every(
      (key) => typeof row[key] === "string",
    )
  )
    return invalid();
  return row;
}
export function validateSummary(row, allowNull = true) {
  if (row == null && allowNull) return null;
  if (
    !object(row) ||
    !id(row.summary_id) ||
    typeof row.summary_text !== "string"
  )
    return invalid();
  return row;
}
export function validateMemory(data) {
  if (data != null && !object(data)) return invalid();
  const pagination = data?.pagination;
  if (
    pagination != null &&
    (!object(pagination) ||
      !Number.isInteger(pagination.page) ||
      pagination.page < 1 ||
      !Number.isInteger(pagination.page_size) ||
      pagination.page_size < 1 ||
      !["facts", "relations"].every(
        (key) =>
          object(pagination[key]) &&
          typeof pagination[key].has_next === "boolean",
      ))
  )
    return invalid();
  return {
    facts: rows(data?.facts, validateFact),
    relations: rows(data?.relations, validateRelation),
    summary: validateSummary(data?.summary),
    pagination: pagination || null,
  };
}
