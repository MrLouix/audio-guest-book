#!/usr/bin/env python3
"""Script de debug pour visualiser les pulsations détectées sur le cadran rotatif.

Ce script affiche en temps réel :
- Chaque impulsion détectée avec timestamp
- Chaque changement d'état dial_active
- Chaque chiffre validé

Usage :
    python3 tests/debug_pulses.py           # simulation, aucune dépendance
    python3 tests/debug_pulses.py --reel    # sur le Raspberry Pi câblé
"""

import datetime
import logging
import sys
import time

import harness  # règle sys.path : doit précéder les imports de src/

import config  # noqa: E402
import gpio_io  # noqa: E402


# Configure logging to show debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s.%(msecs)03d] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)


def format_timestamp() -> str:
    """Return current timestamp formatted for display."""
    return datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]


def test_simule() -> None:
    """Simulation logicielle des événements avec logging."""
    print("\n" + "=" * 60)
    print("MODE SIMULATION - Debug des pulsations")
    print("=" * 60)
    
    inputs = gpio_io.PhoneInputs()
    
    print("\nActions disponibles :")
    print("  d - Simuler décroché")
    print("  r - Simuler raccroché")
    print("  0-9 - Simuler composition d'un chiffre")
    print("  p - Simuler une impulsion seule (pour tester le débounce)")
    print("  q - Quitter")
    print()
    
    while True:
        try:
            cmd = input("Action : ").strip().lower()
            
            if cmd == 'q':
                print("Fin de la simulation.")
                break
            elif cmd == 'd':
                inputs.set_hook(True)
                print(f"[{format_timestamp()}] DECROCHE simulé")
            elif cmd == 'r':
                inputs.set_hook(False)
                print(f"[{format_timestamp()}] RACCROCHE simulé")
            elif cmd in '0123456789':
                chiffre = int(cmd)
                print(f"\n[{format_timestamp()}] --> Composition du chiffre {chiffre}")
                inputs.set_dial_active(True)
                print(f"[{format_timestamp()}] dial_active: False -> True (start rotation)")
                
                nb_pulses = 10 if chiffre == 0 else chiffre
                for i in range(nb_pulses):
                    time.sleep(0.05)  # Small delay between pulses
                    print(f"[{format_timestamp()}] PULSE #{i+1}")
                    inputs.register_pulse()
                
                inputs.set_dial_active(False)
                print(f"[{format_timestamp()}] dial_active: True -> False (end rotation)")
                
                # Check if digit was validated
                digit = inputs.pop_digit()
                if digit is not None:
                    print(f"[{format_timestamp()}] VALIDATED digit: {digit}")
            elif cmd == 'p':
                print(f"[{format_timestamp()}] PULSE simulé (hors rotation)")
                inputs.register_pulse()
            else:
                print("Commande inconnue. Essayez : d, r, 0-9, p, q")
                
        except KeyboardInterrupt:
            print("\nInterrompu par l'utilisateur.")
            break


def test_reel() -> None:
    """Lecture des événements réels depuis le matériel avec debug."""
    print("\n" + "=" * 60)
    print("MODE MATÉRIEL - Debug des pulsations en temps réel")
    print("=" * 60)
    
    try:
        inputs = gpio_io.PhoneInputs()
        
        # Le câblage des GPIO vient du service, et de lui seul : ce script
        # rebranchait ses propres callbacks, qui avaient fini par détecter
        # d'autres fronts que gpio_io.setup. Les impulsions, les changements
        # d'état du cadran et les chiffres validés sont journalisés par
        # gpio_io lui-même — il suffit de laisser passer ses logs DEBUG.
        if gpio_io.GPIO is not None:
            gpio_io.setup(inputs)
            logging.getLogger("gpio_io").setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)

        print("\nLecture en cours... (Ctrl+C pour arrêter)")
        print("-" * 60)
        
        # Initial state
        print(f"[{format_timestamp()}] État initial:")
        print(f"  Crochet: {'DECROCHE' if inputs.is_hook_up() else 'RACCROCHE'}")
        print(f"  Cadran: {'ACTIF' if inputs.is_dial_active() else 'REPOS'}")
        
        # Main loop
        try:
            while True:
                time.sleep(0.5)
                
                # Check for validated digits
                while True:
                    digit = inputs.pop_digit()
                    if digit is None:
                        break
                    print(f"[{format_timestamp()}] VALIDATED digit: {digit}")
                    
        except KeyboardInterrupt:
            print("\n\nArrêt demandé par l'utilisateur.")
            
    finally:
        gpio_io.cleanup()


def main() -> None:
    args = harness.parseur(__doc__).parse_args()
    harness.configurer_logs(args.verbeux)
    
    if args.reel:
        test_reel()
    else:
        test_simule()


if __name__ == "__main__":
    main()
