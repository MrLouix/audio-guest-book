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


class DebugCallback:
    """Callback wrapper that logs all events."""
    
    def __init__(self, inputs: gpio_io.PhoneInputs):
        self.inputs = inputs
        self.pulse_count = 0
        self.start_time = time.time()
    
    def on_pulse(self, channel: int) -> None:
        """Callback for pulse detection."""
        timestamp = format_timestamp()
        self.pulse_count += 1
        print(f"[{timestamp}] PULSE #{self.pulse_count}")
        self.inputs.register_pulse()
    
    def on_offnormal(self, channel: int) -> None:
        """Callback for off-normal detection."""
        timestamp = format_timestamp()
        state = gpio_io.GPIO.input(config.DIAL_OFFNORMAL_PIN)
        is_active = state == gpio_io.DIAL_ACTIVE_LEVEL
        print(f"[{timestamp}] OFFNORMAL: {'ACTIF' if is_active else 'REPOS'}")
        self.inputs.set_dial_active(is_active)
    
    def on_hook(self, channel: int) -> None:
        """Callback for hook detection."""
        timestamp = format_timestamp()
        state = gpio_io.GPIO.input(config.HOOK_PIN)
        is_active = state == gpio_io.HOOK_ACTIVE_LEVEL
        print(f"[{timestamp}] HOOK: {'DECROCHE' if is_active else 'RACCROCHE'}")
        self.inputs.set_hook(is_active)


def test_simule() -> None:
    """Simulation logicielle des événements avec logging."""
    print("\n" + "=" * 60)
    print("MODE SIMULATION - Debug des pulsations")
    print("=" * 60)
    
    inputs = gpio_io.PhoneInputs()
    callbacks = DebugCallback(inputs)
    
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
        
        # Initialisation GPIO
        if gpio_io.GPIO is not None:
            gpio_io.GPIO.setmode(gpio_io.GPIO.BCM)
            
            # Configuration du crochet
            gpio_io.GPIO.setup(config.HOOK_PIN, gpio_io.GPIO.IN,
                             pull_up_down=gpio_io.GPIO.PUD_UP)
            
            # Configuration du cadran
            gpio_io.GPIO.setup(config.DIAL_OFFNORMAL_PIN, gpio_io.GPIO.IN,
                             pull_up_down=gpio_io.GPIO.PUD_UP)
            gpio_io.GPIO.setup(config.DIAL_PULSE_PIN, gpio_io.GPIO.IN,
                             pull_up_down=gpio_io.GPIO.PUD_UP)
            
            # Callbacks avec logging
            callbacks = DebugCallback(inputs)
            
            gpio_io.GPIO.add_event_detect(
                config.HOOK_PIN, gpio_io.GPIO.BOTH,
                callback=callbacks.on_hook,
                bouncetime=int(config.HOOK_DEBOUNCE_SEC * 1000)
            )
            
            gpio_io.GPIO.add_event_detect(
                config.DIAL_OFFNORMAL_PIN, gpio_io.GPIO.BOTH,
                callback=callbacks.on_offnormal,
                bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000)
            )
            
            gpio_io.GPIO.add_event_detect(
                config.DIAL_PULSE_PIN, gpio_io.GPIO.BOTH,
                callback=callbacks.on_pulse,
                bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000)
            )
        
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
