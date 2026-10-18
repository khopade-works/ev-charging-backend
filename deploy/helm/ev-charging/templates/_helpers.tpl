{{- define "ev.tag" -}}{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}

{{- define "ev.selector" -}}
app.kubernetes.io/name: ev-charging
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ev.labels" -}}
{{ include "ev.selector" . }}
app.kubernetes.io/version: {{ include "ev.tag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
