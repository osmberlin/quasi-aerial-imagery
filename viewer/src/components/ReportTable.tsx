/** Key/value table of a run report. Nested objects and arrays are rendered recursively; numbers are shown unformatted. */
export function ReportTable({ data }: { data: Record<string, unknown> }) {
  return (
    <table className="w-full text-xs">
      <tbody>
        {Object.entries(data).map(([key, value]) => (
          <tr key={key} className="border-t border-slate-200 align-top">
            <th scope="row" className="py-1 pr-2 text-left font-medium text-slate-600">
              {key}
            </th>
            <td className="py-1 break-all">
              <ReportValue value={value} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ReportValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-slate-400">–</span>
  if (Array.isArray(value)) {
    const primitive = value.every((item) => item === null || typeof item !== 'object')
    if (primitive) return <span className="font-mono">{value.map(String).join(', ')}</span>
    return (
      <ol className="flex flex-col gap-2">
        {value.map((item, index) => (
          // Report arrays are static data, so the position is a stable key.
          <li key={index}>
            <ReportValue value={item} />
          </li>
        ))}
      </ol>
    )
  }
  if (typeof value === 'object') {
    return <ReportTable data={value as Record<string, unknown>} />
  }
  // Numbers, booleans and strings: JSON keeps the full value without rounding or locale formatting.
  return <span className="font-mono">{JSON.stringify(value)}</span>
}
