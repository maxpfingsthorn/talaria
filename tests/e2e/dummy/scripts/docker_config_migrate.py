from hermes_cli.config import check_config_version, migrate_config


def main():
    cur, latest = check_config_version()
    if cur < latest:
        migrate_config()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
