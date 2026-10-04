FROM ${base}
COPY --chmod=0755 clawvisor-server /clawvisor-server
EXPOSE 25297
USER 65532:65532
ENTRYPOINT ["/clawvisor-server"]
CMD ["server"]
