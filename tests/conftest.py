# Permet à pytest d'importer le package "bot" depuis la racine du projet.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
