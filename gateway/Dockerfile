FROM rust:1-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
COPY --from=build /src/target/release/klipin-gateway /usr/local/bin/klipin-gateway
EXPOSE 8000
CMD ["klipin-gateway"]

