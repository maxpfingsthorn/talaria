FROM ${base}
COPY --chmod=0755 gbrain /usr/local/bin/gbrain
EXPOSE 3131
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/gbrain"]
