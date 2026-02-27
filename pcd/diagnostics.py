import sys
import platform
import os
import json
import argparse
import traceback

def check_environment():
    status = "OK"
    details = {
        "sys_executable": sys.executable,
        "python_version": sys.version,
        "platform_info": platform.platform(),
        "cwd": os.getcwd()
    }
    error = None
    return {"check": "environment", "status": status, "details": details, "error": error}

def check_sys_path():
    status = "OK"
    details = {
        "sys_path_total": len(sys.path),
        "sys_path_top5": sys.path[:5]
    }
    error = None
    return {"check": "sys_path", "status": status, "details": details, "error": error}

def check_imports():
    status = "OK"
    details = {}
    error = None
    
    try:
        import pydantic
        details["pydantic"] = "OK"
    except Exception as e:
        status = "ERROR"
        details["pydantic"] = "FAILED"
        error = {"traceback": traceback.format_exc(), "tip": "A biblioteca 'pydantic' não foi encontrada. Seu ambiente virtual está ativado e as dependências instaladas?"}
        return {"check": "imports", "status": status, "details": details, "error": error}

    try:
        import pcd
        import pcd.core.schema
        details["pcd"] = "OK"
        details["pcd.core.schema"] = "OK"
    except Exception as e:
        status = "ERROR"
        details["pcd"] = "FAILED"
        error = {
            "traceback": traceback.format_exc(), 
            "tip": "Não foi possível importar 'pcd' ou 'pcd.core.schema'. Verifique se você está rodando do root do projeto e se faltou algum __init__.py ou se o VS Code está usando o interpreter correto."
        }
        return {"check": "imports", "status": status, "details": details, "error": error}

    return {"check": "imports", "status": status, "details": details, "error": error}

def check_schema_instantiation():
    status = "OK"
    details = {}
    error = None
    
    try:
        from pcd.core.schema import TripType
        obj = TripType.ONEWAY
        details["instantiate_triptype"] = f"OK: {repr(obj)}"
    except Exception as e:
        status = "ERROR"
        details["instantiate_triptype"] = "FAILED"
        error = {
            "traceback": traceback.format_exc(),
            "tip": "Falha ao instanciar TripType.ONEWAY do schema. Verifique se pcd.core.schema contém TripType."
        }
    
    return {"check": "schema_instantiation", "status": status, "details": details, "error": error}

def run_diagnostics(as_json=False):
    results = [
        check_environment(),
        check_sys_path(),
        check_imports()
    ]
    
    # Só tenta instanciar se os imports passaram
    if results[-1]["status"] == "OK":
        results.append(check_schema_instantiation())

    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Print output in a readable format
    print("="*50)
    print(" PCD Diagnostics Report ".center(50, "="))
    print("="*50)
    
    for r in results:
        print(f"\n[CHECK]: {r['check'].upper()}")
        print(f"Status : {r['status']}")
        print(f"Details:")
        for k, v in r["details"].items():
            if isinstance(v, list):
                print(f"  - {k}:")
                for item in v:
                    print(f"      {item}")
            else:
                print(f"  - {k}: {v}")
        if r["error"]:
            print(f"\n[! ERROR ENCOUNTERED !]")
            print(f"Tip: {r['error'].get('tip', '')}")
            print(f"Traceback:\n{r['error'].get('traceback', '')}")
    
    print("\n" + "="*50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PCD Diagnostics Tool")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    args = parser.parse_args()
    run_diagnostics(as_json=args.json)
