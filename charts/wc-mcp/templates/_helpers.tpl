{{- define "wc-mcp.labels" -}}
app.kubernetes.io/name: wc-mcp
app.kubernetes.io/part-of: urumi-mcp-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}
