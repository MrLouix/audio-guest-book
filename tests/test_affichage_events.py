#!/usr/bin/env python3
"""Script d'affichage des événements téléphone : décroché, raccroché, composition.

Ce script lit et affiche en temps réel :
- les événements de décroché/raccroché du combiné
- les chiffres composés au cadran rotatif

Usage :
    python3 tests/test_affichage_events.py           # simulation, aucune dépendance
    python3 tests/test_affichage_events.py --reel    # sur le Raspberry Pi câblé
"""

import time

import harness                       # règle sys.path : doit précéder les imports de src/

import config                        # noqa: E402
import gpio_io                       # noqa: E402


def afficher_etat_crochet(inputs: gpio_io.PhoneInputs) -> None:
    """Affiche l'état actuel du crochet."""
    if inputs.is_hook_up():
        print("[DECROCHE] - Le combiné est décroché")
    else:
        print("[RACCROCHE] - Le combiné est raccroché")


def afficher_chiffre_compose(inputs: gpio_io.PhoneInputs) -> bool:
    """Vérifie si un chiffre a été composé et l'affiche. Retourne True si un chiffre a été lu."""
    chiffre = inputs.pop_digit()
    if chiffre is not None:
        print(f"  → Chiffre composé : {chiffre}")
        return True
    return False


def afficher_etat_cadran(inputs: gpio_io.PhoneInputs) -> None:
    """Affiche l'état du cadran."""
    if inputs.is_dial_active():
        print(f"  [Cadran en rotation - impulsions: {inputs.has_pulses()}]")
    else:
        print("  [Cadran au repos]")


def test_simule() -> None:
    """Simulation logicielle des événements."""
    print("\n" + "=" * 60)
    print("MODE SIMULATION - Appuyez sur Entrée pour simuler les actions")
    print("=" * 60)
    
    inputs = gpio_io.PhoneInputs()
    
    # État initial
    print("\nÉtat initial :")
    afficher_etat_crochet(inputs)
    afficher_etat_cadran(inputs)
    
    # Simulation interactive
    print("\nActions disponibles :")
    print("  d - Simuler décroché")
    print("  r - Simuler raccroché")
    print("  0-9 - Simuler composition d'un chiffre")
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
                print("\n---")
                afficher_etat_crochet(inputs)
            elif cmd == 'r':
                inputs.set_hook(False)
                print("\n---")
                afficher_etat_crochet(inputs)
            elif cmd in '0123456789':
                chiffre = int(cmd)
                # Simuler la composition d'un chiffre au cadran
                inputs.set_dial_active(True)
                for _ in range(10 if chiffre == 0 else chiffre):
                    inputs.register_pulse()
                inputs.set_dial_active(False)
                
                print("\n---")
                print(f"Rotation du cadran pour le chiffre {chiffre}...")
                afficher_chiffre_compose(inputs)
                afficher_etat_cadran(inputs)
            else:
                print("Commande inconnue. Essayez : d, r, 0-9, q")
                
        except KeyboardInterrupt:
            print("\nInterrompu par l'utilisateur.")
            break


def test_reel() -> None:
    """Lecture des événements réels depuis le matériel."""
    print("\n" + "=" * 60)
    print("MODE MATÉRIEL - Lecture en temps réel depuis le Raspberry Pi")
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
            
            # Anti-rebond
            gpio_io.GPIO.add_event_detect(
                config.HOOK_PIN, gpio_io.GPIO.BOTH,
                callback=lambda channel: inputs.set_hook(
                    gpio_io.GPIO.input(channel) == gpio_io.HOOK_ACTIVE_LEVEL),
                bouncetime=int(config.HOOK_DEBOUNCE_SEC * 1000)
            )
            
            gpio_io.GPIO.add_event_detect(
                config.DIAL_OFFNORMAL_PIN, gpio_io.GPIO.BOTH,
                callback=lambda channel: inputs.set_dial_active(
                    gpio_io.GPIO.input(channel) == gpio_io.DIAL_ACTIVE_LEVEL),
                bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000)
            )
            
            gpio_io.GPIO.add_event_detect(
                config.DIAL_PULSE_PIN, gpio_io.GPIO.RISING,
                callback=lambda channel: inputs.register_pulse(),
                bouncetime=int(config.DIAL_DEBOUNCE_SEC * 1000)
            )
        
        print("\nLecture en cours... (Ctrl+C pour arrêter)")
        print("-" * 60)
        
        # État initial
        afficher_etat_crochet(inputs)
        afficher_etat_cadran(inputs)
        
        # Boucle de lecture
        hook_precedent = inputs.is_hook_up()
        
        try:
            while True:
                time.sleep(0.1)
                
                # Vérifier changement d'état du crochet
                hook_actuel = inputs.is_hook_up()
                if hook_actuel != hook_precedent:
                    print("\n---")
                    afficher_etat_crochet(inputs)
                    hook_precedent = hook_actuel
                
                # Vérifier si un chiffre a été composé
                if afficher_chiffre_compose(inputs):
                    afficher_etat_cadran(inputs)
                    
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
