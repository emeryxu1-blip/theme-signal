# Display

Use this file for frontend-facing value interpretation rules.

## Percentage display

If response `attr.value_type` is:

- `ratio`
- `ratio2`

the frontend must display the value with `%`.

This is a product rule for the AInvest quote consumer of this API and should be applied even though raw unit handling in the source docs is more detailed.

## Empty and null values

- `snapshot` may return `{"v": null}` for a valid indicator with no value on a symbol.
- `series` may return an empty `value` array for a valid indicator with no historical data.
- These are acceptable data outcomes, not parameter errors.

## Invalid indicators

- Invalid indicators may simply be absent from the response.
- Do not assume the response echoes every requested indicator.
- Use `req_unique_id` to map returned indicators back to the request.

## Sorting behavior

For `snapshot`, when a sorted metric is null:

- it is placed after non-null values
- null-valued rows do not become request errors

## Error messaging

Focus on parameter and structure problems only, for example:

- missing required `attr`
- unsupported `time_range.type`
- invalid symbol-source combination for the business scenario
