#!/usr/bin/env python
import os, sys
import numpy as np
import pandas as pd
import srcs.hypothesis_recovery_src as hr
from scipy.sparse import load_npz
import argparse
import srcs.utils as utils
import warnings
import json
warnings.filterwarnings("ignore")
from tqdm import tqdm
from loguru import logger
logger.remove()
logger.add(sys.stdout, format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {message}", level="INFO")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="This script estimates the abundance of microorganisms from a "
                    "reference database matrix and metagenomic sample.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--database_prefix', help='Reference database matrix in npz format', required=True)
    parser.add_argument('--sample_file', help='Metagenomic sample in .sig format', required=True)
    parser.add_argument('--significance', type=float, help='Minimum probability of individual true negative.',
                        required=False, default=0.99)
    parser.add_argument('--keep_raw', action='store_false', help='Keep raw results in output file.')
    parser.add_argument('--show_present_only', action='store_true', help='Only show organisms present in sample.')
    parser.add_argument('--min_coverage', nargs="+", type=float, help='To compute false negative weight, assume each organism '
                                                           'has this minimum coverage in sample. Should be between '
                                                           '0 and 1, with 0 being the most sensitive (and least '
                                                           'precise) and 1 being the most precise (and least '
                                                           'sensitive).', required=False, default=[1, 0.5, 0.1, 0.05, 0.01])
    parser.add_argument('--out_filename', help='output filename', required=False, default='result.xlsx')
    parser.add_argument('--outdir', help='path to output directory', required=True)

    # parse the arguments
    args = parser.parse_args()
    prefix = args.database_prefix + '_'  # prefix for the database files
    sample_file = args.sample_file  # location of sample.sig file (y vector)
    significance = args.significance  # Minimum probability of individual true negative.
    keep_raw = args.keep_raw  # Keep raw results in output file.
    show_present_only = args.show_present_only  # Only show organisms present in sample.
    min_coverage_list = args.min_coverage  # a list of percentages of unique k-mers covered by reads in the sample.
    out_filename = args.out_filename  # output filename
    outdir = args.outdir  # csv destination for results

    # check if the json file exists
    utils.check_file_existence(prefix + 'config.json', f'Config file {prefix + "config.json"} does not exist. '
                                                      f'Please run make_training_data_from_sketches.py first.')
    # load the config file, ksize, and ani_thresh
    json_file = prefix + 'config.json'
    config = json.load(open(json_file, 'r'))
    try:
        ksize = config['ksize']
    except KeyError as e:
        raise KeyError('ksize not found in config file.') from e
    try:
        ani_thresh = config['ani_thresh']
    except KeyError as exc:
        raise KeyError('ani_thresh not found in config file.') from exc

    # check that ksize is an integer
    if not isinstance(ksize, int):
        raise ValueError('ksize must be an integer.')
    # check if min_coverage is between 0 and 1
    for x in min_coverage_list:
        if not (0 <= x <= 1):
            raise ValueError(f'One of min_coverages you provided {x} is not between 0 and 1. Please check your input.')

    # Get the training data names
    ref_matrix = prefix + 'ref_matrix_processed.npz'
    hash_to_idx_file = prefix + 'hash_to_col_idx.pkl'
    processed_org_file = prefix + 'processed_org_idx.csv'

    # make sure all these files exist
    utils.check_file_existence(ref_matrix, f'Reference matrix file {ref_matrix} '
                                           f'does not exist. Please run make_training_data_from_sketches.py first.')
    utils.check_file_existence(hash_to_idx_file, f'Hash to index file {hash_to_idx_file} '
                                                 f'does not exist. Please run make_training_data_from_sketches.py first.')
    utils.check_file_existence(processed_org_file, f'Processed organism file {processed_org_file} '
                                                   f'does not exist. Please run make_training_data_from_sketches.py first.')

    # load the training data
    logger.info('Loading reference matrix, hash to index dictionary, and organism data.')
    reference_matrix = load_npz(ref_matrix)
    hash_to_idx = utils.load_hashes(hash_to_idx_file)
    organism_data = pd.read_csv(processed_org_file)

    logger.info('Loading sample signature.')
    # get the sample y vector (indexed by hash/k-mer, with entry = number of times k-mer appears in sample)
    sample_sig = utils.load_signature_with_ksize(sample_file, ksize)

    logger.info('Computing sample vector.')
    # get the hashes in the sample signature (it's for a single sample)
    sample_hashes = sample_sig.minhash.hashes
    sample_vector = utils.compute_sample_vector(sample_hashes, hash_to_idx)

    # get the number of kmers in the sample from the scaled sketch
    num_sample_kmers = utils.get_num_kmers(sample_sig, scale=False)  # TODO: might not save this for time reasons
    # get the number of unique kmers in the sample
    num_unique_sample_kmers = len(sample_hashes)

    # prep the output data structure, copying over the organism data
    recov_org_data = organism_data.copy()
    recov_org_data['num_total_kmers_in_sample_sketch'] = num_sample_kmers  # TODO: might not save this for time reasons
    recov_org_data['num_exclusive_kmers_in_sample_sketch'] = num_unique_sample_kmers
    recov_org_data['sample_scale_factor'] = sample_sig.minhash.scaled
    recov_org_data['min_coverage'] = 1

    # check that the sample scale factor is the same as the genome scale factor for all organisms
    sample_diff_idx = np.where(recov_org_data['sample_scale_factor'].ne(
        recov_org_data['genome_scale_factor']).to_list())[0].tolist()
    sample_diffs = recov_org_data['organism_name'].iloc[sample_diff_idx]
    if not sample_diffs.empty:
        raise ValueError(f'Sample scale factor does not equal genome scale factor for organism '
                         f'{sample_diffs.iloc[0]} and {len(sample_diffs) - 1} others.')

    # compute hypothesis recovery
    logger.info('Computing hypothesis recovery.')
    hyp_recovery_df, nontriv_flags = hr.hypothesis_recovery(
        reference_matrix, sample_vector, ksize, significance=significance,
        ani_thresh=ani_thresh, min_coverage=1)

    # Boolean indicating whether genome shares at least one k-mer with sample
    recov_org_data['nontrivial_overlap'] = nontriv_flags

    # for each of the columns of hyp_recovery_df, add it to the recov_org_data
    for col in hyp_recovery_df.columns:
        recov_org_data[col] = hyp_recovery_df[col]

    # remove from recov_org_data all those with non-trivial overlap 0
    recov_org_data = recov_org_data[recov_org_data['nontrivial_overlap'] == 1]

    # remove unnecessary columns
    remove_cols = ['original_index', 'processed_index', 'nontrivial_overlap', 'alt_confidence_mut_rate', 'sample_scale_factor'] + [col for col in recov_org_data.columns if '_wo_coverage' in col]
    recov_org_data_filtered = recov_org_data.drop(columns=remove_cols)
    recov_org_data_filtered.rename(columns={'genome_scale_factor': 'scale_factor'}, inplace=True)

    # save the results into Excel file
    logger.info(f'Saving results to {outdir}.')
    if not isinstance(out_filename, str) and out_filename != '':
        out_filename = 'result.xlsx'
    min_coverage_list = list(set(min_coverage_list))
    min_coverage_list.sort(reverse=True)

    # save the original result
    if keep_raw:
        recov_org_data_filtered.to_excel(os.path.join(outdir, out_filename), sheet_name=f'raw_result', engine='openpyxl', index=False)


    with pd.ExcelWriter(os.path.join(outdir, out_filename), engine='openpyxl', mode='a') as writer:
        for min_coverage in min_coverage_list:
            temp_output_result = recov_org_data_filtered.copy()
            temp_output_result['min_coverage'] = min_coverage
            temp_output_result['acceptance_threshold_with_coverage'] = min_coverage * temp_output_result['acceptance_threshold_with_coverage']
            temp_output_result['actual_confidence_with_coverage'] = min_coverage * temp_output_result['actual_confidence_with_coverage']
            temp_output_result['alt_confidence_mut_rate_with_coverage'] = min_coverage * temp_output_result['alt_confidence_mut_rate_with_coverage']
            temp_output_result['in_sample_est'] = (temp_output_result['num_matches'] >= temp_output_result['acceptance_threshold_with_coverage']) \
                                                    & (temp_output_result['num_matches'] != 0) & (temp_output_result['acceptance_threshold_with_coverage'] != 0)
            if show_present_only:
                temp_output_result = temp_output_result[temp_output_result['in_sample_est'] == True]
            temp_output_result.to_excel(writer, sheet_name=f'min_coverage{min_coverage}', index=False)

