# Contributing to IOP

Start with a reproducible instrument interaction or research workflow. Include the relevant model, driver, software version, operation inputs and observed output. Distinguish physical measurements from simulator results.

- Python service, adapters and SDK: `src/ai4instruments/iop/`.
- Rust process supervisor: `iop/daemon/`.
- TypeScript SDK: `iop/clients/typescript/`.
- Device and platform descriptions: `iop/schema/`, `iop/instruments/`.
- Host connections: `iop/connect/`.
- Executable experiment packages: `examples/iop/`.

For Python changes run `python -m unittest discover -s tests -v`. For TypeScript changes run `npm ci` and `npm test` in `iop/clients/typescript`. The release workflow builds on six native OS/architecture combinations. Add cases that exercise an actual changed behavior, including interrupted operations when relevant.

Preserve existing experiment IDs, operation records, SDK imports and data directories. Prefer extending the existing scheduler and driver interface over a second execution mechanism.

Software contributions use MIT; documentation and original figures use CC BY 4.0.
