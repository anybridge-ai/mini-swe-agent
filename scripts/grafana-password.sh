#!/bin/bash
# Get Grafana admin password from Kubernetes secret
echo "User: admin"
echo -n "Password: "
kubectl get secret --namespace monitoring grafana -o jsonpath="{.data.admin-password}" | base64 --decode
echo
