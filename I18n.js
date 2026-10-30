.pragma library

function tr(table, source, values) {
  const template = Object.prototype.hasOwnProperty.call(table, source) ? table[source] : source
  const fields = values || {}
  return template.replace(/\{([A-Za-z_][A-Za-z_0-9]*)\}/g, function(match, name) {
    return Object.prototype.hasOwnProperty.call(fields, name) ? String(fields[name]) : match
  })
}
