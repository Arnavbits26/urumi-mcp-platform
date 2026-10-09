{{- define "store.labels" -}}
app.kubernetes.io/part-of: urumi-store
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}
