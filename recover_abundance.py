import numpy as np
import cvxpy as cp
import csv
import sample_vector as sv
from scipy.sparse import load_npz
import argparse
import utils
import warnings
warnings.filterwarnings("ignore")


# inputs: matrix A, vector y, weight w
# output: estimate vector x and metadata
def recover_abundance_from_vectors(A, y, w):
    K, N = np.shape(A)
    x = cp.Variable(N)
    u = cp.Variable(K)
    v = cp.Variable(K)
    tau = 1 / (w + 1)
    ones_K = np.ones(K)
    objective = cp.Minimize(
        tau * (ones_K @ u) + (1 - tau) * (ones_K @ v)
    )
    constraints = [
        x >= 0,
        u >= 0,
        v >= 0,
        u - v + (A @ x) == y,
    ]
    prob = cp.Problem(objective, constraints)
    result = prob.solve(solver=cp.SCIPY, verbose=False)
    return x.value


def recover_abundance_from_files(matrix_file, sample_file, ksize, w, output_filename):
    prefix = args.ref_file.split('ref_matrix_processed.npz')[0]
    hash_to_idx_file = prefix + 'hash_to_col_idx.csv'
    processed_org_file = prefix + 'processed_org_idx.csv'
    
    reference_matrix = load_npz(matrix_file)
    sample_vector = sv.sample_vector_from_files(sample_file, hash_to_idx_file, ksize)
    abundance = recover_abundance_from_vectors(reference_matrix, sample_vector, w)
    support = np.nonzero(abundance)
    organisms = utils.load_processed_organisms(processed_org_file)
    write_abundance_results(abundance, organisms, output_filename)
    return abundance


def write_abundance_results(abundance_vector, organisms, output_filename):
    f = open(output_filename, 'w', newline='', encoding='utf-8')
    writer = csv.writer(f)
    writer.writerow(['organism name', 'estimated abundance'])
    for i, org in enumerate(organisms):
        writer.writerow([org, abundance_vector[i]])
    f.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="This script estimates the abundance of microorganisms from a reference database matrix and metagenomic sample.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--ref_file', help='Reference database matrix in npz format', required=True)
    parser.add_argument('--ksize', type=int, help='Size of kmers used in sketch', required=True)
    parser.add_argument('--sample_file', help='Metagenomic sample in .sig format', required=True)
    # parser.add_argument('--hash_file', help='csv file of hash values in database sketch')
    # parser.add_argument('--org_file', help='csv list of organisms in database')
    parser.add_argument('--w', type=float, help='False positive weight', required=True)
    parser.add_argument('--outfile', help='csv destination for results', required=True)
    args = parser.parse_args()
    
    recover_abundance_from_files(args.ref_file, args.sample_file, args.ksize, args.w, args.outfile)