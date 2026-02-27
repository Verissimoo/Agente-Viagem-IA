import argparse
import sys

def main():
    parser = argparse.ArgumentParser(
        prog="python -m pcd",
        description="Módulo principal do pacote pcd"
    )
    
    parser.add_argument(
        "--version", 
        action="version", 
        version="pcd 0.1.0"
    )

    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # Comando 'doctor'
    doctor_parser = subparsers.add_parser("doctor", help="Executa o diagnóstico de ambiente e pacote")
    doctor_parser.add_argument("--json", action="store_true", help="Saída do diagnóstico em JSON")

    # Parse args
    args = parser.parse_args()

    if args.command == "doctor":
        from pcd.diagnostics import run_diagnostics
        run_diagnostics(as_json=args.json)
        sys.exit(0)

    # Sem argumentos, exibe o help
    if len(sys.argv) == 1 or args.command is None:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
